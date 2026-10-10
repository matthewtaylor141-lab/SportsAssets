"""RC6.3 FEED-1: THE FEED CACHE KEEPS WHAT SOMEONE NEEDS, AND NAMES WHAT IT
EVICTED -- so a fixture Pinnacle has not posted can be called NOT YET POSTED
on evidence, and a fixture the venue lists is not evicted while a churning
in-play record stays.

THE DEFECT (software-reds audit of the RC6.3b packet hour, 2026-10-10
09:54Z; research-sql run 38071612807 at 17:24Z): the feed held 3,806 of
4,000 events with 281 evictions since the owner started; `_bound` evicted
the least-recently-touched record whatever its start or who needed it;
`pinnapi_names.absence` kept the naming doubt for EVERY miss once the
process-wide `events_evicted` counter was above zero (NOT_YET_POSTED was
named in one hour of 24 -- the hour the counter was still zero); and a slate
of kick-offs sharing "United" / "City" / "State" at one start named every
other game of the slate. 35 events / hour read PINNAPI_PRIMARY_NO_EXACT_
FIXTURE (SOFTWARE); 5 MLS games whose metered fallback was then refused
QUOTE_STALE_ON_ARRIVAL would have priced from the feed had their fixtures
stayed.

WHAT THIS FILE PINS (each fails on the base f971d665, passes here):
  (a) a counted eviction ELSEWHERE no longer keeps the doubt: the global
      counter > 0 with no near tombstone -> no_candidate_near_start True
      and the codes [NOT_YET_POSTED, PAYLOAD_HAS_NO_PINNACLE_BOOK];
  (b) two MLS kick-offs at one start sharing "United", one posted: the
      unposted one is NOT_YET_POSTED; a shared non-affiliative token
      ("New York") is a candidate until the record is CLAIMED by the other
      metered event, then NOT_YET_POSTED only with the claim;
  (c) an NCAAF slate of 40 games at one start sharing "State";
  (d) a near tombstone at the same start keeps NO_EXACT (named);
  (e) a tombstone-ring overflow inside its window keeps the doubt, a
      counter the ring cannot account for keeps it, and the doubt lifts
      once the window has passed;
  (f) FeedCache(max_events=50) fed 200 events with 10 quiet protected
      fixtures: all 10 survive, the unprotected go first, farthest start
      first, least-recently-touched last;
  (g) the 5 MLS NO_EXACT-beside-stale shapes of the packet hour (H1) price
      from the feed once retained;
  (h) the first-loss census classes the resulting row EXTERNAL and a
      NO_EXACT row with its evidence codes SOFTWARE by NO_EXACT;
  (i) the ledger summary round-trips through real Postgres;
plus the census -> set_protected plumbing, the heartbeat's retention block,
the off-loop snapshot build's tombstones, the taxonomy of the new codes,
and that nothing else moved (caps, tolerance, window, the 36 h scan's stop
words).
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import json
import time
import types
import uuid

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import coverage_first_loss as CFL
from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_names as N
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets.workers import ext_pinnacle_loop as loop
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

T = _dt.datetime(2026, 10, 14, 23, 30, tzinfo=_dt.timezone.utc).timestamp()
NOW = T - 4 * 86400
HOUR = 3600.0
DAY = 86400.0
ML = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline", "period": 0,
      "status": "open",
      "prices": [{"designation": "home", "price": -125},
                 {"designation": "away", "price": 110},
                 {"designation": "draw", "price": 300}]}


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat() \
        .replace("+00:00", "Z")


def _rec(fid, start, home, away, *, markets=False, league=None):
    return {"id": fid, "startTime": iso(start), "isLive": False,
            "type": "matchup", "units": "Regular",
            "league": league or {"id": 1, "name": "L"},
            "participants": [{"name": home, "alignment": "home"},
                             {"name": away, "alignment": "away"}],
            "markets": [copy.deepcopy(ML)] if markets else []}


def _feed(fixtures, *, sport_id=1, now=NOW, max_events=None, markets=False,
          ring_size=None, protected=()):
    """A granted, synced FeedCache on a pinned clock holding `fixtures`
    [(id, start, home, away)] of one sport, delivered in one prematch
    snapshot in that order (so a small `max_events` evicts as the snapshot
    lands)."""
    kw = {"max_events": max_events} if max_events else {}
    c = F.FeedCache(**kw)
    c.offload_snapshots = False
    c.clock = lambda: now
    if ring_size is not None:
        c.tombstones = F.Tombstones(size=ring_size)
    if protected and callable(getattr(c, "set_protected", None)):
        c.set_protected(protected)
    e = c.new_connection([("prematch", sport_id)])
    evs = [_rec(fid, start, h, w, markets=markets)
           for fid, start, h, w in fixtures]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": sport_id,
             "ts": (now - 60) * 1000, "events": evs}, epoch=e,
            received_ms=(now - 60) * 1000 + 5)
    c.authority = types.SimpleNamespace(
        granted=True, synced=True, epoch=c.authority.epoch, reason=None,
        state=lambda: {"granted": True, "synced": True})
    return c


def _add(c, fid, start, home, away, *, sport_id=1, ts=None):
    """One more record, delivered as a prematch_matchups frame (the cache
    bounds itself as it lands)."""
    now = c.clock()
    c.apply({"type": "prematch_matchups", "sport_id": sport_id,
             "ts": (ts if ts is not None else now) * 1000,
             "data": [_rec(fid, start, home, away)]},
            epoch=c.authority.epoch, received_ms=now * 1000)


def _metered(home, away, *, sport_key="soccer_usa_mls", start=T, books=None,
             eid=None):
    """The metered provider's event as production carried it: no Pinnacle
    book unless `books` says so."""
    return {"id": eid or uuid.uuid4().hex, "sport_key": sport_key,
            "home_team": home, "away_team": away, "commence_time": iso(start),
            "bookmakers": ([{"key": "draftkings", "markets": []}]
                           if books is None else books)}


def _select(cache, event, *, family="soccer", at=None):
    why: dict = {}
    at = NOW if at is None else at
    got = P.select(cache, event,
                   loop.pinnacle_h2h(event, received_at=at),
                   family=family, sharp_books=set(loop.SHARP_BOOKS),
                   at=at, runtime_id="r1", explain=why)
    return got, why


DCU, NYRB = "D.C. United", "New York Red Bulls"


# ═════════════════════════════════════════════════════════════════════
# 0 · NOTHING ELSE MOVED
# ═════════════════════════════════════════════════════════════════════

def test_the_caps_the_tolerance_the_window_and_the_wide_scan_are_unchanged():
    assert F.MAX_EVENTS == 4000 and F.MAX_MARKETS == 120_000
    assert P.START_TOLERANCE_S == 90 * 60
    assert N.ABSENCE_WINDOW_S == 36 * 3600
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    # the 36 h scan's stop words are the short list they were
    assert N.ABSENCE_STOP == frozenset(("fc", "sc", "cf", "afc", "club",
                                        "clube", "de", "do", "da", "the",
                                        "of"))
    # the near-start question drops the affiliative words, and only them
    assert N.NEAR_START_STOP >= N.ABSENCE_STOP | {"state", "united", "city",
                                                  "fc"}
    assert N.NEAR_START_STOP - N.ABSENCE_STOP == {"state", "st", "united",
                                                  "utd", "city"}
    # the ring keeps a tombstone at least the absence window plus the
    # match tolerance
    assert F.TOMBSTONE_RETENTION_S >= N.ABSENCE_WINDOW_S + P.START_TOLERANCE_S
    assert F.TOMBSTONE_RING >= 4 * F.MAX_EVENTS
    # NO_EXACT is still ours; NOT_YET_POSTED still EXTERNAL
    assert CFL.classify(P.R_NO_EXACT)["class"] == RT.SOFTWARE
    assert CFL.classify(loop.R_FIXTURE_NOT_YET_POSTED)["class"] == \
        CFL.EXTERNAL


# ═════════════════════════════════════════════════════════════════════
# (a) A COUNTED EVICTION ELSEWHERE NO LONGER KEEPS THE DOUBT
# ═════════════════════════════════════════════════════════════════════

def test_a_a_counted_eviction_elsewhere_no_longer_keeps_the_doubt():
    """max_events 1: the snapshot's second record evicts the first (Atlanta
    United v Orlando City, 20 h before the start -- it names a participant
    TOKEN inside the 36 h window, so the miss is NO_EXACT, not NOT_IN_FEED).
    The counter is 1 and the only tombstone is 20 h away from the start:
    no candidate near the start, NOT_YET_POSTED with the payload's absence
    beside it. On the base any counter > 0 kept the doubt."""
    c = _feed([(1, T - 20 * HOUR, "Atlanta United", "Orlando City"),
               (3, T, "Flamengo", "Palmeiras")], max_events=1)
    assert c.counts["events_evicted"] == 1 and 1 not in c.events
    ev = _metered(DCU, NYRB)
    got, why = _select(c, ev)
    assert got is None and why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is True
    assert loop.no_pinnacle_codes(why, ev) == [
        loop.R_FIXTURE_NOT_YET_POSTED, loop.R_PAYLOAD_HAS_NO_PINNACLE]
    # the evidence: the evicted record named a team 20 h away, not near
    assert ab["evicted_total"] == 1 and ab["tombstones_of_sport"] == 1
    assert ab["tombstones_near"] == [] and ab["blocked_by"] == []
    assert ab["tombstones_sharing"] == ["Atlanta United"]
    assert ab["evictions_unaccounted"] == 0 and ab["tombstone_overflow"] \
        is False
    posted = loop.fixture_not_yet_posted(why, ev)
    assert posted["feed_evicted"] == 1 and posted["tombstones_near"] == []
    # the row's evidence codes: no blocker, one summary
    codes = loop.absence_evidence_codes(why, ev)
    assert len(codes) == 1
    assert loop.parse_absence_summary(codes[0]) == {
        "sport_records": 1, "feed_evicted": 1, "named_near_start": 0,
        "tombstones_near": 0, "protected_eviction": 0}
    # the index path answers the same
    idx = P.fixture_index(c)
    ex2: dict = {}
    assert P.match_event(c, ev, "soccer", index=idx, explain=ex2)[1] == \
        P.R_NO_EXACT
    assert ex2["absence"]["no_candidate_near_start"] is True


def test_a_the_tombstone_is_resolved_when_its_event_comes_back():
    c = _feed([(1, T - 20 * HOUR, "Atlanta United", "Orlando City"),
               (3, T, "Flamengo", "Palmeiras")], max_events=2)
    _add(c, 4, T + DAY, "Santos", "Gremio")
    assert c.counts["events_evicted"] == 1
    gone = [t["id"] for t in c.tombstones.within(NOW)]
    assert len(gone) == 1
    fid = gone[0]
    # the provider re-sends it: no doubt about it any more
    rec = {1: (T - 20 * HOUR, "Atlanta United", "Orlando City"),
           3: (T, "Flamengo", "Palmeiras"), 4: (T + DAY, "Santos", "Gremio")}
    _add(c, fid, *rec[fid])
    assert fid in c.events
    assert fid not in c.tombstones.by_id and c.tombstones.resolved == 1
    assert [t["id"] for t in c.tombstones.within(NOW) if t["id"] == fid] == []


# ═════════════════════════════════════════════════════════════════════
# (b) TWO MLS KICK-OFFS AT ONE START
# ═════════════════════════════════════════════════════════════════════

def test_b1_two_kickoffs_sharing_united_the_unposted_one_is_not_yet_posted():
    """Atlanta United v Orlando City is in the feed at the start; D.C.
    United v New York Red Bulls is not, and its payload has no Pinnacle
    book. "United" is an affiliative word, not a shared name: no candidate
    near the start -> NOT_YET_POSTED (the base kept NO_EXACT)."""
    c = _feed([(1, T, "Atlanta United", "Orlando City")])
    ev = _metered(DCU, NYRB)
    got, why = _select(c, ev)
    assert got is None and why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is True
    assert ab["named_near_start"] == [] and ab["sharing"] == ["Atlanta United"]
    assert loop.no_pinnacle_codes(why, ev)[0] == loop.R_FIXTURE_NOT_YET_POSTED
    # the posted one is still matched exactly
    hit, reason = P.match_event(
        c, _metered("Atlanta United", "Orlando City"), "soccer")
    assert reason is None and hit[0] == 1
    # the 36 h scan did not move: outside the tolerance the same record is
    # still "names a team" (the absence stays ours: NO_EXACT, not
    # NOT_IN_FEED), and a record sharing a NON-affiliative token at the
    # start is still a candidate
    c = _feed([(2, T, "New York City", "Toronto")])
    got, why = _select(c, ev)
    assert why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is False
    assert ab["named_near_start"] == ["New York City"]
    assert ab["blocked_by"] == [N.B_NAMED_NEAR_START]
    assert loop.no_pinnacle_codes(why, ev)[0] == P.R_NO_EXACT


def test_b2_a_shared_token_record_is_ignored_only_once_another_event_claims_it():
    """New York City v Toronto is in the feed at the start; New York Red
    Bulls v D.C. United is not. "New York" is a real shared token, so the
    record is a candidate -- until the metered event New York City FC v
    Toronto FC matches it one-to-one (canonically): then it is that game
    and cannot be the Red Bulls' under other names."""
    c = _feed([(2, T, "New York City", "Toronto")])
    unposted = _metered(NYRB, DCU)
    got, why = _select(c, unposted)
    assert why["reason"] == P.R_NO_EXACT
    assert why["provenance"]["fixture_match"]["absence"][
        "no_candidate_near_start"] is False
    assert loop.no_pinnacle_codes(why, unposted)[0] == P.R_NO_EXACT
    # the posted game is asked for (the same cycle): an exact one-to-one
    # match by another METERED event claims the record
    posted = _metered("New York City FC", "Toronto FC")
    got, why2 = _select(c, posted)
    assert why2["reason"] != P.R_NO_EXACT      # matched (no money line held)
    assert len(c.claims) == 1
    got, why = _select(c, unposted)
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is True
    assert ab["records_claimed_by_another_event"] == 1
    assert loop.no_pinnacle_codes(why, unposted) == [
        loop.R_FIXTURE_NOT_YET_POSTED, loop.R_PAYLOAD_HAS_NO_PINNACLE]
    # a PinnAPI-native seed claims nothing: its identity is the fixture
    c = _feed([(2, T, "New York City", "Toronto")])
    seed = dict(_metered("New York City", "Toronto"),
                pinnapi_native={"fixture_id": 2, "family": "soccer"})
    P.match_event(c, seed, "soccer")
    assert len(c.claims) == 0
    # an event of the SAME two names claims nothing against itself (the
    # same game listed twice): the record stays a candidate for it
    c = _feed([(2, T, "New York City", "Toronto")])
    _select(c, posted)
    dup = _metered("NYC FC", "Toronto FC")  # misses; its names differ
    got, why = _select(c, dup)
    assert why["reason"] == P.R_NO_EXACT
    # ...but a claim by an event whose names ARE the asking names covers
    # nothing: the record is a candidate again
    twin = _metered("New York City FC", "Toronto FC")
    got, why = _select(c, twin)
    assert why["reason"] != P.R_NO_EXACT      # it simply matches
    # the record's names changing since the claim (a frame renames the
    # away side) voids the claim: the record is a candidate again
    c = _feed([(2, T, "New York City", "Toronto")])
    _select(c, posted)
    assert len(c.claims) == 1
    _add(c, 2, T, "New York City", "Columbus")
    got, why = _select(c, unposted)
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is False
    assert ab["named_near_start"] == ["New York City"]
    assert ab["records_claimed_by_another_event"] == 0


# ═════════════════════════════════════════════════════════════════════
# (c) AN NCAAF SLATE OF 40 GAMES AT ONE START SHARING "STATE"
# ═════════════════════════════════════════════════════════════════════

def _ncaaf_slate(n=40, start=T):
    schools = [s for s, _m in N.NCAAF_SCHOOL_MASCOT if s.endswith(" state")
               and s not in ("kansas state", "iowa state")]
    others = [s for s, _m in N.NCAAF_SCHOOL_MASCOT if not s.endswith(" state")
              and s not in ("kansas", "tcu", "texas christian")]
    out = []
    for i in range(n):
        out.append((1000 + i, start, schools[i % len(schools)].title(),
                    others[i % len(others)].title()))
    return out


def test_c_an_ncaaf_slate_sharing_state_at_one_start_is_not_a_candidate():
    """Forty games at the Saturday start, every one with a "State" school;
    Kansas State v TCU is not among them and the metered payload carries no
    Pinnacle book: NOT_YET_POSTED (the base: "state" was a shared token
    with every one of the 40, NO_EXACT)."""
    c = _feed(_ncaaf_slate(), sport_id=5)
    assert len(c.events) == 40
    ev = _metered("Kansas State Wildcats", "TCU Horned Frogs",
                  sport_key="americanfootball_ncaaf")
    got, why = _select(c, ev, family="football")
    assert got is None and why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["sport_records"] == 40
    assert ab["no_candidate_near_start"] is True
    assert ab["named_near_start"] == []
    assert loop.no_pinnacle_codes(why, ev) == [
        loop.R_FIXTURE_NOT_YET_POSTED, loop.R_PAYLOAD_HAS_NO_PINNACLE]
    # a game at the start sharing a REAL token (Kansas v Iowa State) is a
    # candidate the names might hide: NO_EXACT stays, and the row says why
    c = _feed(_ncaaf_slate() + [(2000, T, "Kansas", "Iowa State")],
              sport_id=5)
    got, why = _select(c, ev, family="football")
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is False
    assert ab["named_near_start"] == ["Kansas"]
    assert ab["blocked_by"] == [N.B_NAMED_NEAR_START]
    codes = loop.absence_evidence_codes(why, ev)
    assert codes[0] == loop.R_ABSENCE_BLOCKED_NAMED_NEAR_START
    assert loop.parse_absence_summary(codes[-1])["named_near_start"] == 1
    assert loop.no_pinnacle_codes(why, ev)[0] == P.R_NO_EXACT
    # the slate itself is still matched canonically, game by game (its
    # first game: the first "State" school against the first other one)
    first = _ncaaf_slate()[0]
    assert first == (1000, T, "Appalachian State", "Air Force")
    hit, reason = P.match_event(
        c, _metered("Appalachian State Mountaineers", "Air Force Falcons",
                    sport_key="americanfootball_ncaaf"), "football")
    assert reason is None and hit[0] == 1000


# ═════════════════════════════════════════════════════════════════════
# (d) A NEAR TOMBSTONE AT THE SAME START KEEPS NO_EXACT
# ═════════════════════════════════════════════════════════════════════

def test_d_a_near_tombstone_at_the_same_start_keeps_no_exact_and_names_it():
    """The fixture itself (DC United v New York Red Bulls, at the start) was
    evicted: it may have been ours to keep. NO_EXACT stays, the blocker is
    named on the row, NOT_YET_POSTED is not."""
    c = _feed([(5, T, "DC United", "New York Red Bulls"),
               (3, T, "Flamengo", "Palmeiras")], max_events=1)
    assert 5 not in c.events and c.counts["events_evicted"] == 1
    ev = _metered(DCU, NYRB)
    got, why = _select(c, ev)
    assert got is None and why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is False
    assert ab["tombstones_near"] == ["DC United", "New York Red Bulls"]
    assert ab["blocked_by"] == [N.B_TOMBSTONE_NEAR_START]
    assert loop.fixture_not_yet_posted(why, ev) is None
    assert loop.no_pinnacle_codes(why, ev) == [P.R_NO_EXACT,
                                               loop.R_PAYLOAD_HAS_NO_PINNACLE]
    codes = loop.absence_evidence_codes(why, ev)
    assert codes[0] == loop.R_ABSENCE_BLOCKED_TOMBSTONE_NEAR_START
    s = loop.parse_absence_summary(codes[-1])
    assert s["tombstones_near"] == 2 and s["feed_evicted"] == 1
    assert s["protected_eviction"] == 0
    # a protected fixture evicted (the cap bit into the protected set) is
    # flagged on the summary
    c = _feed([(5, T, "DC United", "New York Red Bulls"),
               (3, T, "Flamengo", "Palmeiras")], max_events=1, protected=[5])
    assert c.counts["events_evicted_protected"] == 1
    got, why = _select(c, ev)
    s = loop.parse_absence_summary(loop.absence_evidence_codes(why, ev)[-1])
    assert s["protected_eviction"] == 1


# ═════════════════════════════════════════════════════════════════════
# (e) AN OVERFLOW INSIDE THE WINDOW KEEPS THE DOUBT; A COUNTER THE RING
#     CANNOT PLACE KEEPS IT; THE DOUBT LIFTS WITH THE WINDOW
# ═════════════════════════════════════════════════════════════════════

def test_e_overflow_keeps_the_doubt_until_the_window_has_passed():
    far = [(10 + i, T + (3 + i) * DAY, "Club %d" % i, "Team %d" % i)
           for i in range(5)]
    c = _feed(far + [(3, T, "Flamengo", "Palmeiras")], max_events=1,
              ring_size=2)
    assert c.counts["events_evicted"] == 5
    assert c.tombstones.overflowed == 3 and c.tombstones.recorded == 5
    assert c.tombstones.overflow_inside(NOW) is True
    ev = _metered(DCU, NYRB)
    got, why = _select(c, ev)
    assert why["reason"] == P.R_NO_EXACT          # never NOT_IN_FEED
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["absent"] is False and ab["tombstone_overflow"] is True
    assert ab["no_candidate_near_start"] is False
    assert ab["blocked_by"] == [N.B_TOMBSTONE_OVERFLOW]
    assert loop.absence_evidence_codes(why, ev)[0] == \
        loop.R_ABSENCE_BLOCKED_TOMBSTONE_OVERFLOW
    assert loop.no_pinnacle_codes(why, ev)[0] == P.R_NO_EXACT
    # the window passes: whatever was dropped has aged out of every
    # question, and the two tombstones still held have aged out too
    c.clock = lambda: NOW + F.TOMBSTONE_RETENTION_S + 1
    assert c.tombstones.overflow_inside(c.clock()) is False
    assert c.tombstones.within(c.clock()) == []
    got, why = _select(c, ev, at=c.clock())
    ab = (why.get("provenance") or {}).get("fixture_match", {}).get("absence")
    assert ab["no_candidate_near_start"] is True and ab["blocked_by"] == []
    assert why["reason"] == N.R_NOT_IN_FEED       # nothing names a team now


def test_e_a_counter_the_ring_cannot_account_for_keeps_the_doubt():
    """The guard the older tests lean on: a cache whose counter says more
    evictions than its ring recorded (a direct write, never production's)
    keeps every doubt -- NO_EXACT, named."""
    c = _feed([(1, T - 20 * HOUR, "Atlanta United", "Orlando City"),
               (3, T, "Flamengo", "Palmeiras")])
    c.counts["events_evicted"] = 1
    ev = _metered(DCU, NYRB)
    got, why = _select(c, ev)
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["evictions_unaccounted"] == 1
    assert ab["no_candidate_near_start"] is False
    assert ab["blocked_by"] == [N.B_EVICTIONS_UNACCOUNTED]
    assert loop.no_pinnacle_codes(why, ev)[0] == P.R_NO_EXACT
    assert loop.absence_evidence_codes(why, ev)[0] == \
        loop.R_ABSENCE_BLOCKED_EVICTIONS_UNACCOUNTED
    # and `absent` is never claimed over it either
    c = _feed([(3, T + 3 * DAY, "Flamengo", "Palmeiras")])
    c.counts["events_evicted"] = 1
    got, why = _select(c, ev)
    assert why["reason"] == P.R_NO_EXACT
    assert why["provenance"]["fixture_match"]["absence"]["absent"] is False


def test_e_the_pure_absence_pass_takes_tombstones_claims_and_the_flags():
    recs = [{"sport_id": 1, "startTime": iso(T), "id": 7,
             "participants": [{"name": "Flamengo"}, {"name": "Palmeiras"}]}]
    kw = dict(sport_id=1, start=T, home=DCU, away=NYRB, family="soccer",
              tolerance_s=P.START_TOLERANCE_S)
    # no ring given: a bare counter keeps the doubt, exactly as before
    ab = N.absence(recs, evicted=1, **kw)
    assert ab["no_candidate_near_start"] is False
    assert ab["blocked_by"] == [N.B_EVICTIONS_UNACCOUNTED]
    assert "no_candidate_near_start" not in N.absence(
        recs, sport_id=1, start=T, home="a", away="b", family="soccer")
    # a ring that accounts for the counter, tombstone far away: no doubt
    far = [{"sport_id": 1, "start_s": T - 20 * HOUR, "home": "Atlanta United",
            "away": "Orlando City", "evicted_at": NOW, "protected": False}]
    ab = N.absence(recs, evicted=1, tombstones=far, **kw)
    assert ab["no_candidate_near_start"] is True and ab["absent"] is False
    assert ab["tombstones_sharing"] == ["Atlanta United"]
    # near, sharing a real token: a doubt, named
    near = [dict(far[0], start_s=T, home="DC United")]
    ab = N.absence(recs, evicted=1, tombstones=near, **kw)
    assert ab["tombstones_near"] == ["DC United"]
    assert ab["blocked_by"] == [N.B_TOMBSTONE_NEAR_START]
    # near but only an affiliative word in common: no candidate
    ab = N.absence(recs, evicted=1, tombstones=[dict(far[0], start_s=T)],
                   **kw)
    assert ab["no_candidate_near_start"] is True
    # a tombstone of another sport is nobody's business here
    ab = N.absence(recs, evicted=1, tombstones=[dict(near[0], sport_id=5)],
                   **kw)
    assert ab["tombstones_of_sport"] == 0
    assert ab["blocked_by"] == [N.B_EVICTIONS_UNACCOUNTED]
    # the overflow flag
    ab = N.absence(recs, evicted=0, tombstones=[], overflow=True, **kw)
    assert ab["blocked_by"] == [N.B_TOMBSTONE_OVERFLOW]
    assert ab["absent"] is False
    # a claim by another event ignores the record for `near`; a claim by
    # an event of the asking names does not
    nyc = [{"sport_id": 1, "startTime": iso(T), "id": 2,
            "participants": [{"name": "New York City"}, {"name": "Toronto"}]}]
    claims = {2: {"event_id": "x", "names": frozenset(("new york city fc",
                                                       "toronto fc")),
                  "fixture_names": frozenset(("new york city", "toronto"))}}
    ab = N.absence(nyc, tombstones=[], claims=claims, **kw)
    assert ab["no_candidate_near_start"] is True
    assert ab["records_claimed_by_another_event"] == 1
    ab = N.absence(nyc, tombstones=[], claims={}, **kw)
    assert ab["named_near_start"] == ["New York City"]
    own = {2: dict(claims[2], names=frozenset((N._fold(DCU), N._fold(NYRB))))}
    ab = N.absence(nyc, tombstones=[], claims=own, **kw)
    assert ab["no_candidate_near_start"] is False
    # the wide (36 h) scan still counts the claimed record as naming a team
    assert ab["sharing"] == ["New York City"]


# ═════════════════════════════════════════════════════════════════════
# (f) PROTECTED FIXTURES SURVIVE; THE UNPROTECTED GO FIRST, FARTHEST FIRST
# ═════════════════════════════════════════════════════════════════════

def test_f_protected_fixtures_survive_and_the_unprotected_go_farthest_first():
    c = F.FeedCache(max_events=50)
    c.offload_snapshots = False
    c.clock = lambda: NOW
    ep = c.new_connection([("prematch", 1)])
    # ten quiet fixtures, far out (5-14 days: the first to go by distance,
    # the first to go by recency), protected as the census would
    quiet = [_rec(i, NOW + (4 + i) * DAY, "Quiet %d" % i, "Still %d" % i)
             for i in range(1, 11)]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": NOW * 1000, "events": quiet}, epoch=ep,
            received_ms=NOW * 1000)
    assert c.set_protected(range(1, 11)) == 20       # ints and their text
    # 190 more, one frame each, at distinct starts 1..190 h out, in a
    # shuffled order so recency and distance disagree
    hours = [(i * 37) % 190 + 1 for i in range(190)]
    assert len(set(hours)) == 190
    for i, h in enumerate(hours):
        c.apply({"type": "prematch_matchups", "sport_id": 1,
                 "ts": (NOW + i) * 1000,
                 "data": [_rec(100 + i, NOW + h * HOUR, "Home %d" % i,
                               "Away %d" % i)]},
                epoch=ep, received_ms=(NOW + i) * 1000)
    assert len(c.events) == 50
    assert all(i in c.events for i in range(1, 11)), "a protected one went"
    # the unprotected survivors: the record the last frame delivered (a
    # record is never evicted by its own frame) and the 39 nearest starts
    # of every other -- each frame evicted the farthest unprotected record
    # then in the cache
    survivors = sorted(F._start_s(ev["startTime"]) for eid, ev in
                       c.events.items() if eid >= 100)
    want = sorted(NOW + h * HOUR
                  for h in sorted(hours[:-1])[:39] + [hours[-1]])
    assert survivors == want
    assert c.counts["events_evicted"] == 150
    assert c.counts["events_evicted_unprotected"] == 150
    assert c.counts.get("events_evicted_protected", 0) == 0
    assert c.tombstones.recorded == 150 and c.tombstones.unaccounted(150) == 0
    assert c.tombstones.overflowed == 0
    # the farthest went first: the very first eviction (the 41st
    # unprotected record arriving) took the farthest of the first forty,
    # and no record among the final nearest 39 was ever evicted
    tomb_hours = [round((t["start_s"] - NOW) / HOUR)
                  for t in c.tombstones.within(NOW)]
    assert tomb_hours[0] == max(hours[:40])
    assert min(tomb_hours) > sorted(hours[:-1])[38]
    r = c.retention_status(now_s=NOW)
    assert r["protected_events_held"] == 10 and r["protected_ids"] == 20
    assert r["evicted_unprotected"] == 150 and r["evicted_protected"] == 0
    assert r["tombstones"]["held"] == 150
    # the heartbeat carries it (bounded: no id list)
    cen = c.census(now_ms=NOW * 1000)
    assert cen["retention"]["evicted_total"] == 150
    assert cen["counts"]["events_evicted_unprotected"] == 150
    assert "protected_ids" in cen["retention"]
    assert not any(isinstance(v, (list, set)) for v in
                   cen["retention"].values())
    # only when nothing unprotected is left does a protected one go, and
    # that is counted apart -- the live child of a protected fixture is
    # protected with it
    c2 = F.FeedCache(max_events=2)
    c2.offload_snapshots = False
    c2.clock = lambda: NOW
    ep2 = c2.new_connection([("prematch", 1), ("live", 1)])
    c2.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
              "ts": NOW * 1000, "events": [
                  _rec(1, NOW + DAY, "A", "B"), _rec(2, NOW + 5 * DAY, "C",
                                                     "D")]},
             epoch=ep2, received_ms=NOW * 1000)
    c2.set_protected([1])
    kid = dict(_rec(9, NOW + DAY, "A", "B"), parentId=1, isLive=True)
    c2.apply({"type": "live", "sport_id": 1, "op": "upd", "ts": NOW * 1000,
              "rec": kid}, epoch=ep2, received_ms=NOW * 1000)
    assert set(c2.events) == {1, 9}, "the unprotected far one went first"
    assert c2.counts["events_evicted_unprotected"] == 1
    c2.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
              "data": [_rec(3, NOW + 2 * HOUR, "E", "F")]},
             epoch=ep2, received_ms=NOW * 1000)
    assert c2.counts["events_evicted_protected"] == 1
    assert 3 in c2.events
    assert [t["protected"] for t in c2.tombstones.within(NOW)] == [False, True]


def test_f_a_record_is_never_evicted_by_its_own_frame_and_ties_go_by_recency():
    c = F.FeedCache(max_events=2)
    c.offload_snapshots = False
    c.clock = lambda: NOW
    ep = c.new_connection([("prematch", 1)])
    # three records at ONE start (ties): the least-recently-touched goes
    for i, (fid, h, a) in enumerate([(1, "A", "B"), (2, "C", "D"),
                                     (3, "E", "F")]):
        c.apply({"type": "prematch_matchups", "sport_id": 1,
                 "ts": (NOW + i) * 1000,
                 "data": [_rec(fid, NOW + DAY, h, a)]},
                epoch=ep, received_ms=(NOW + i) * 1000)
    assert set(c.events) == {2, 3}
    # a far record arriving onto a full cache is kept by its own frame
    c.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
             "data": [_rec(4, NOW + 30 * DAY, "G", "H")]},
            epoch=ep, received_ms=NOW * 1000)
    assert 4 in c.events and set(c.events) == {3, 4}
    # ...and goes first on the next frame
    c.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
             "data": [_rec(5, NOW + 2 * DAY, "I", "J")]},
            epoch=ep, received_ms=NOW * 1000)
    assert set(c.events) == {3, 5}
    # an unreadable start is farthest of all: kept by its own frame (the
    # farther of the two others, 5 at 2 d, goes), gone on the next
    c.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
             "data": [dict(_rec(6, NOW, "K", "L"), startTime=None)]},
            epoch=ep, received_ms=NOW * 1000)
    assert set(c.events) == {3, 6}
    c.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
             "data": [_rec(7, NOW + 3 * DAY, "M", "N")]},
            epoch=ep, received_ms=NOW * 1000)
    assert set(c.events) == {3, 7}
    assert [t["id"] for t in c.tombstones.within(NOW)] == [1, 2, 4, 5, 6]
    # the markets bound evicts whole events in the same order
    c3 = F.FeedCache(max_events=100, max_markets=2)
    c3.offload_snapshots = False
    c3.clock = lambda: NOW
    ep3 = c3.new_connection([("prematch", 1)])
    c3.set_protected([1])
    c3.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
              "ts": NOW * 1000, "events": [
                  _rec(1, NOW + 9 * DAY, "A", "B", markets=True),
                  _rec(2, NOW + DAY, "C", "D", markets=True),
                  _rec(3, NOW + 2 * DAY, "E", "F", markets=True)]},
             epoch=ep3, received_ms=NOW * 1000)
    assert len(c3.quotes) <= 2
    assert 1 in c3.events, "the protected far one stayed"
    assert 2 not in c3.events, "the unprotected farther one went first"
    assert c3.counts["events_evicted"] == 1
    assert c3.counts["events_deleted"] == 0, "an eviction is not a delete"


def test_f_the_off_loop_snapshot_build_leaves_its_tombstones_at_the_swap():
    n = F.SNAPSHOT_OFFLOOP_MIN_EVENTS + 20
    evs = [_rec(10_000 + i, NOW + (i % 40 + 1) * HOUR, "H %d" % i,
                "A %d" % i, markets=True) for i in range(n)]

    async def go():
        c = F.FeedCache(max_events=50)
        assert c.offload_snapshots is True
        c.clock = lambda: NOW
        c.set_protected([10_000, 10_001])
        ep = c.new_connection([("prematch", 1)])
        got = c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
                       "ts": NOW * 1000, "events": evs}, epoch=ep,
                      received_ms=NOW * 1000)
        assert got == F.APPLY_BUILDING
        for _ in range(4000):
            if c._pending is None:
                break
            await asyncio.sleep(0.005)
        assert c._pending is None
        return c, ep
    c, ep = asyncio.run(go())
    assert c.counts["snapshot_builds_swapped_in"] == 1
    assert len(c.events) == 50 and {10_000, 10_001} <= set(c.events)
    assert c.counts["events_evicted"] == n - 50
    assert c.tombstones.recorded == n - 50
    assert c.tombstones.unaccounted(c.counts["events_evicted"]) == 0
    assert c.tombstones.within(NOW) and all(
        t["id"] not in c.events for t in c.tombstones.within(NOW))
    # an evicted one re-delivered after the swap is resolved
    gone = c.tombstones.within(NOW)[0]["id"]
    c.apply({"type": "prematch_matchups", "sport_id": 1, "ts": NOW * 1000,
             "data": [evs[gone - 10_000]]}, epoch=ep, received_ms=NOW * 1000)
    assert gone in c.events and gone not in c.tombstones.by_id


# ═════════════════════════════════════════════════════════════════════
# (g) THE FIVE MLS NO_EXACT-BESIDE-STALE SHAPES PRICE FROM THE FEED
# ═════════════════════════════════════════════════════════════════════

#: H1: the 5 MLS events of the RC6.3b packet hour whose ledger rows carried
#: QUOTE_STALE_ON_ARRIVAL with PINNAPI_PRIMARY_NO_EXACT_FIXTURE beside it
#: (tests/fixtures/rc63_sw_reds_104_events.json on rc6/sw-reds-replay, the
#: H0 harness; research-sql 38055963121) -- the metered provider's names,
#: and the feed's own renderings of the same clubs (pinnapi_names: the
#: soccer affiliation "FC" is dropped canonically)
H1_SHAPES = (
    ("Atlanta United FC", "FC Cincinnati", "Atlanta United", "FC Cincinnati",
     "2026-10-10T23:30:00Z"),
    ("Colorado Rapids", "San Jose Earthquakes", "Colorado Rapids",
     "San Jose Earthquakes", "2026-10-11T01:30:00Z"),
    ("New England Revolution", "Seattle Sounders FC",
     "New England Revolution", "Seattle Sounders", "2026-10-10T23:30:00Z"),
    ("Minnesota United FC", "Houston Dynamo", "Minnesota United",
     "Houston Dynamo", "2026-10-11T00:30:00Z"),
    ("Los Angeles FC", "Vancouver Whitecaps FC", "Los Angeles FC",
     "Vancouver Whitecaps", "2026-10-11T02:30:00Z"),
)


def _h1_feed(*, protect: bool):
    """The feed holding the five fixtures with a change-dated money line
    (a prematch_markets list that moved the price), then flooded with 60
    other soccer records onto a cap of 8. `protect` names the five as the
    venue census would; without it the base's recency rule evicts them."""
    t0 = P.epoch("2026-10-10T20:00:00Z")
    c = F.FeedCache(max_events=8)
    c.offload_snapshots = False
    c.clock = lambda: t0
    ep = c.new_connection([("prematch", 1)])
    evs = []
    for i, (_mh, _ma, fh, fa, start) in enumerate(H1_SHAPES):
        evs.append(_rec(700 + i, P.epoch(start), fh, fa, markets=True))
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": (t0 - 120) * 1000, "events": evs}, epoch=ep,
            received_ms=(t0 - 120) * 1000)
    moved = copy.deepcopy(ML)
    moved["prices"][0]["price"] = -130
    for i in range(len(H1_SHAPES)):
        c.apply({"type": "prematch_markets", "sport_id": 1,
                 "ts": (t0 - 5) * 1000, "matchup_id": 700 + i,
                 "data": [copy.deepcopy(moved)]}, epoch=ep,
                received_ms=(t0 - 5) * 1000 + 2)
    if protect and callable(getattr(c, "set_protected", None)):
        c.set_protected([700 + i for i in range(len(H1_SHAPES))])
    for i in range(60):
        c.apply({"type": "prematch_matchups", "sport_id": 1,
                 "ts": (t0 - 4) * 1000,
                 "data": [_rec(900 + i, t0 + (2 + i % 9) * DAY,
                               "Clube %d" % i, "Esporte %d" % i)]},
                epoch=ep, received_ms=(t0 - 4) * 1000)
    c.authority = types.SimpleNamespace(granted=True, synced=True,
                                        epoch=c.authority.epoch, reason=None)
    return c, t0


def _h1_event(mh, ma, start, *, at):
    """The metered event as the ledger saw it: a Pinnacle book delivered
    45 s old (the fallback that was then refused QUOTE_STALE_ON_ARRIVAL)."""
    pin = [{"key": "pinnacle", "last_update": iso(at - 45),
            "markets": [{"key": "h2h", "outcomes": [
                {"name": mh, "price": 2.4}, {"name": ma, "price": 2.9},
                {"name": "Draw", "price": 3.4}]}]}]
    return _metered(mh, ma, start=P.epoch(start), books=pin)


def test_g_the_five_h1_shapes_price_from_the_feed_once_retained():
    c, t0 = _h1_feed(protect=True)
    assert len(c.events) == 8
    assert all(700 + i in c.events for i in range(5)), "a fixture went"
    for mh, ma, _fh, _fa, start in H1_SHAPES:
        ev = _h1_event(mh, ma, start, at=t0)
        got, why = _select(c, ev, at=t0)
        assert got is not None, (mh, ma, why)
        assert got["reference_input"]["provider"] == P.PROVIDER, (mh, ma, why)
        assert got["reference_input"]["fixture_match"]["name_match"] in (
            P.MATCH_EXACT, P.MATCH_CANONICAL)
        assert why == {}
    # the counterfactual: the same flood with nothing protected evicts
    # them by distance (they are the nearest starts of the cache, so
    # here they SURVIVE on distance alone -- the base's recency rule is
    # what evicted them; `_h1_feed(protect=False)` on the base falls back)
    c, t0 = _h1_feed(protect=False)
    for mh, ma, _fh, _fa, start in H1_SHAPES:
        got, why = _select(c, _h1_event(mh, ma, start, at=t0), at=t0)
        assert got is not None
        assert got["reference_input"]["provider"] == P.PROVIDER


# ═════════════════════════════════════════════════════════════════════
# (h) THE CENSUS CLASSES THE RESULTING ROW
# ═════════════════════════════════════════════════════════════════════

def test_h_the_census_classes_a_not_yet_posted_row_external_and_no_exact_ours():
    c = _feed([(1, T - 20 * HOUR, "Atlanta United", "Orlando City"),
               (3, T, "Flamengo", "Palmeiras")], max_events=1)
    ev = _metered(DCU, NYRB, eid="e1")
    got, why = _select(c, ev)
    codes = loop.no_pinnacle_codes(why, ev) + loop.absence_evidence_codes(
        why, ev)
    out = loop._event_outcome(codes)
    assert out["outcome"] == "REFUSED"
    assert out["first_refusal"] == loop.R_FIXTURE_NOT_YET_POSTED
    assert codes[-1].startswith(loop.ABSENCE_SUMMARY_PREFIX)
    row = {"outcome": "REFUSED", "first_refusal": out["first_refusal"],
           "codes": out["codes"], "stage": "1_PROBABILITY", "reach": 2,
           "sport_key": "soccer_usa_mls", "provider_event_id": "e1",
           "family": "soccer"}
    fl = CFL.first_loss_of_event(row, [], [], valuations_read=True,
                                 decisions_read=True)
    assert fl["class"] == CFL.EXTERNAL
    assert fl["code"] == loop.R_FIXTURE_NOT_YET_POSTED
    cen = CFL.census([row], [], [])
    assert cen["totals"]["by_class"][CFL.EXTERNAL] == 1
    assert cen["totals"]["by_class"][RT.UNCLASSIFIED] == 0
    # a NO_EXACT row with its blocker and summary beside it: SOFTWARE by
    # NO_EXACT, never by the evidence
    c = _feed([(5, T, "DC United", "New York Red Bulls"),
               (3, T, "Flamengo", "Palmeiras")], max_events=1)
    got, why = _select(c, ev)
    codes = loop.no_pinnacle_codes(why, ev) + loop.absence_evidence_codes(
        why, ev)
    out = loop._event_outcome(codes)
    assert out["first_refusal"] == P.R_NO_EXACT
    row = dict(row, first_refusal=out["first_refusal"], codes=out["codes"])
    fl = CFL.first_loss_of_event(row, [], [], valuations_read=True,
                                 decisions_read=True)
    assert fl["class"] == RT.SOFTWARE and fl["code"] == P.R_NO_EXACT
    assert loop.R_ABSENCE_BLOCKED_TOMBSTONE_NEAR_START in fl["codes"]
    cen = CFL.census([row], [], [])
    assert cen["totals"]["by_class"][RT.SOFTWARE] == 1
    assert cen["totals"]["by_class"][RT.UNCLASSIFIED] == 0
    # the summary alone is never a refusal
    assert loop._event_outcome([codes[-1]])["first_refusal"] is None


def test_h_every_new_code_is_classified_staged_and_the_summary_is_evidence():
    blockers = sorted(set(loop.ABSENCE_BLOCKER_CODES.values()))
    assert len(blockers) == 9
    no_exact = RT.classify(P.R_NO_EXACT)
    for code in blockers:
        k = RT.classify(code)
        assert k["classified"] and k["class"] == RT.SOFTWARE, code
        # the identity question itself: NO_EXACT's own family and stage
        assert (k["family"], k["stage"]) == (no_exact["family"],
                                             no_exact["stage"]), code
        assert k["stage"] == "EVENT_IDENTITY", code
        assert ext.STAGE_OF[code] == "1_PROBABILITY", code
        assert ext.EVALUABILITY_OF[code] == ext.COULD_NOT_EVALUATE, code
        assert CFL.classify(code)["class"] == RT.SOFTWARE
    for b in (N.B_NAMED_NEAR_START, N.B_TOMBSTONE_NEAR_START,
              N.B_TOMBSTONE_OVERFLOW, N.B_EVICTIONS_UNACCOUNTED,
              N.B_NO_RECORD_OF_SPORT):
        assert b in loop.ABSENCE_BLOCKER_CODES
    s = loop.absence_summary_code({"sport_records": 12, "feed_evicted": 3,
                                   "named_near_start": 0,
                                   "tombstones_near": 1,
                                   "protected_eviction": 1})
    assert s == ("PINNAPI_ABSENCE_SUMMARY:sport_records=12;feed_evicted=3;"
                 "named_near_start=0;tombstones_near=1;protected_eviction=1")
    assert loop.parse_absence_summary(s) == {
        "sport_records": 12, "feed_evicted": 3, "named_near_start": 0,
        "tombstones_near": 1, "protected_eviction": 1}
    assert loop.parse_absence_summary("PINNAPI_PRIMARY_NO_EXACT_FIXTURE") \
        is None
    assert loop.parse_absence_summary(s + ";extra=1") is None
    k = RT.classify(s)
    assert k["code"] == loop.R_ABSENCE_SUMMARY and not k["classified"]
    assert "DECLARED_NOT_A_REFUSAL" in k["why"]
    # any other WS refusal carries no evidence codes
    assert loop.absence_evidence_codes({"reason": F.R_NO_AUTHORITY},
                                       _metered(DCU, NYRB)) == []
    assert loop.absence_evidence_codes(None, None) == []
    # a seed, a Pinnacle book in the payload, no payload: each a named
    # blocker on a NO_EXACT row
    why = {"reason": P.R_NO_EXACT, "provenance": {"fixture_match": {
        "absence": {"sport_records": 3, "evicted_total": 0,
                    "named_near_start": [], "tombstones_near": [],
                    "no_candidate_near_start": True, "blocked_by": []}}}}
    seed = dict(_metered(DCU, NYRB), pinnapi_native={"fixture_id": 9})
    assert loop.absence_evidence_codes(why, seed)[0] == \
        loop.R_ABSENCE_BLOCKED_NATIVE_SEED
    pin = _metered(DCU, NYRB, books=[{"key": "pinnacle", "markets": [
        {"key": "h2h", "outcomes": []}]}])
    assert loop.absence_evidence_codes(why, pin)[0] == \
        loop.R_ABSENCE_BLOCKED_PINNACLE_IN_PAYLOAD
    assert loop.absence_evidence_codes(why, dict(_metered(DCU, NYRB),
                                                 bookmakers=None))[0] == \
        loop.R_ABSENCE_BLOCKED_NO_METERED_PAYLOAD
    assert loop.absence_evidence_codes(
        {"reason": P.R_NO_EXACT}, _metered(DCU, NYRB))[0] == \
        loop.R_ABSENCE_BLOCKED_NO_ABSENCE_PASS
    # a fallback's reference_input carries the same answer
    ref = {"fallback_reason": P.R_NO_EXACT, "provider": P.LEGACY_PROVIDER,
           "feed_read": why["provenance"]}
    assert loop.absence_evidence_codes(ref, pin)[0] == \
        loop.R_ABSENCE_BLOCKED_PINNACLE_IN_PAYLOAD
    assert loop.absence_summary(ref, pin)["sport_records"] == 3


# ═════════════════════════════════════════════════════════════════════
# (i) THE LEDGER SUMMARY ROUND-TRIPS THROUGH REAL POSTGRES
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_i_the_ledger_row_carries_the_evidence_through_postgres():
    conn = await H.connect()
    try:
        c = _feed([(5, T, "DC United", "New York Red Bulls"),
                   (3, T, "Flamengo", "Palmeiras")], max_events=1)
        ev = _metered(DCU, NYRB, eid="pg-e1")
        got, why = _select(c, ev)
        codes = loop.no_pinnacle_codes(why, ev) + \
            loop.absence_evidence_codes(why, ev)
        summary = loop.absence_summary(why, ev)
        out = loop._event_outcome(codes)
        row = {"sport_key": "soccer_usa_mls", "family": "soccer",
               "queue_position": 0, "provider_event_id": "pg-e1",
               "home": DCU, "away": NYRB, "commence_time": iso(T),
               "global_slug": None, "us_market_slug": None,
               "stage": "1_PROBABILITY", "provider_lag_s": None,
               "our_processing_s": None, "quote_age_s": None,
               "mapped_by": None, "global_refusal_replaced": None}
        row.update(out)
        res = await loop._persist_candidate_outcomes(
            conn, cycle_at=time.time(), rows=[row])
        assert res["ok"] is True and res["rows"] == 1, res
        try:
            back = await conn.fetchrow(
                "SELECT outcome, first_refusal, codes FROM "
                "ext_candidate_outcomes WHERE cycle_id = $1", res["cycle_id"])
            assert back["outcome"] == "REFUSED"
            assert back["first_refusal"] == P.R_NO_EXACT
            got_codes = json.loads(back["codes"]) if isinstance(
                back["codes"], str) else list(back["codes"])
            assert got_codes[:2] == [P.R_NO_EXACT,
                                     loop.R_PAYLOAD_HAS_NO_PINNACLE]
            assert loop.R_ABSENCE_BLOCKED_TOMBSTONE_NEAR_START in got_codes
            parsed = [loop.parse_absence_summary(x) for x in got_codes]
            parsed = [p for p in parsed if p is not None]
            assert parsed == [{k: summary[k]
                               for k in loop.ABSENCE_SUMMARY_FIELDS}]
            assert parsed[0]["tombstones_near"] == 2
            assert parsed[0]["feed_evicted"] == 1
            # the census reads the row as SOFTWARE by NO_EXACT
            fl = CFL.first_loss_of_event(
                {"outcome": back["outcome"],
                 "first_refusal": back["first_refusal"],
                 "codes": got_codes, "stage": "1_PROBABILITY", "reach": 2},
                [], [], valuations_read=True, decisions_read=True)
            assert fl["class"] == RT.SOFTWARE and fl["code"] == P.R_NO_EXACT
        finally:
            await conn.execute(
                "DELETE FROM ext_candidate_outcomes WHERE cycle_id = $1",
                res["cycle_id"])
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE CENSUS TELLS THE CACHE WHAT TO KEEP
# ═════════════════════════════════════════════════════════════════════

def test_the_venue_counterpart_states_name_every_fixture_the_venue_lists():
    view = {1: [
        {"id": 11, "quote_id": 11, "home": "Atlanta United",
         "away": "Orlando City", "start": T, "live": False},
        {"id": 12, "quote_id": 12, "home": "Atlanta United",
         "away": "Orlando City", "start": T + 5 * HOUR, "live": False},
        {"id": 13, "quote_id": 99, "home": "LA Galaxy", "away": "Austin",
         "start": T, "live": True},
        {"id": 14, "quote_id": 14, "home": "Austin", "away": "Houston Dynamo",
         "start": T, "live": False},
        {"id": 15, "quote_id": 15, "home": "Nobody", "away": "Else",
         "start": T, "live": False}],
        5: [{"id": 51, "quote_id": 51, "home": "Troy", "away": "Southern Miss",
             "start": T, "live": False}]}
    venue = {(1, "a"): ({"Atlanta United", "Orlando City"}, {T}, "MATCHED"),
             (1, "b"): ({"LA Galaxy", "Austin FC"}, {T}, "NO_FEED_EVENT"),
             (1, "c"): ({"Nobody"}, {T}, "STRUCTURED_PARTICIPANTS_NOT_TWO"),
             (5, "d"): ({"Troy", "Southern Miss"}, {T + 4 * HOUR},
                        "NO_FEED_EVENT")}
    got = C.venue_counterpart_fixture_ids(venue, view)
    assert got["ids"] == [11, 12, 13, 99, 51]
    assert got["by_state"] == {"MATCHED": 1, "AMBIGUOUS": 0,
                               "TIME_MISMATCH": 2, "PARTICIPANT_MISMATCH": 1}
    # two fixtures that fit one venue event: both are kept
    venue = {(1, "a"): ({"Atlanta United", "Orlando City"}, {T}, "AMBIGUOUS")}
    view[1][1]["start"] = T + HOUR
    got = C.venue_counterpart_fixture_ids(venue, view)
    assert got["ids"] == [11, 12] and got["by_state"]["AMBIGUOUS"] == 2
    # the census carries the list and its counts
    rows = [{"sports_type": "soccer", "event_slug": "a", "kind": "side",
             "line": None, "team_name": n, "team_league": "mls",
             "game_start": T, "event_title": "Atlanta United v Orlando City"}
            for n in ("Atlanta United", "Orlando City")]
    cen = C.census(rows, view, subscribed_sports={1}, synced=True, now=NOW)
    assert cen["venue_counterpart_fixture_ids"] == [11, 12]
    assert cen["venue_counterpart_by_state"]["AMBIGUOUS"] == 2


def test_protect_fixtures_joins_the_counterparts_the_held_targets_and_the_seeds(
        monkeypatch):
    from sportsassets import pinnapi_held as PH
    from sportsassets import pinnapi_reactive as RX
    c = _feed([(1, T, "A", "B"), (2, T, "C", "D"), (3, T, "E", "F"),
               (4, T, "G", "H")])
    watch = PH.HeldWatch()
    watch.set_targets({"slug-1": (3, None), "slug-2": (None, "UNMATCHED")})
    monkeypatch.setattr(PH, "WATCH", watch)

    class _Sched:
        seeds = {4: {"registered_at": NOW}, 5: {"registered_at": NOW - 9e9}}

        def _seed_live(self, eid, seed):
            return eid == 4
    monkeypatch.setattr(RX, "ACTIVE", _Sched())
    got = FR.protect_fixtures(c, [1, 2], now=NOW)
    assert got == {"venue_counterpart": 2, "held_targets": 1,
                   "reactive_seeds": 1, "protected": 8}
    assert c.is_protected(1) and c.is_protected(2) and c.is_protected(3)
    assert c.is_protected(4) and not c.is_protected(5)
    assert c.protected_set_at == NOW
    # no scheduler, no held watch: the counterparts alone, never a raise
    monkeypatch.setattr(RX, "ACTIVE", None)
    monkeypatch.setattr(PH, "WATCH", PH.HeldWatch())
    got = FR.protect_fixtures(c, ["7"], now=NOW)
    assert got["protected"] == 2 and c.is_protected(7) and c.is_protected("7")
    assert not c.is_protected(1)
    # a cache that cannot be told is reported, not raised at
    got = FR.protect_fixtures(object(), [1], now=NOW)
    assert got["error"] == "AttributeError"


def test_the_census_pass_protects_the_counterparts_and_keeps_the_ids_off_the_heartbeat(
        monkeypatch):
    import threading
    where: dict = {}

    def _census(rows, view, **kw):
        where["thread"] = threading.current_thread().name
        return {"ok": True, "venue_counterpart_fixture_ids": [1, 3],
                "venue_counterpart_by_state": {"MATCHED": 2}}

    class _Conn:
        async def fetch(self, sql, *a):
            return []

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *e):
            return False

    class _Pool:
        def acquire(self, *a, **kw):
            return _Acq()

    cache = _feed([(1, T, "A", "B"), (2, T, "C", "D"), (3, T, "E", "F")])

    class _Owner:
        sport_ids = [1]
    _Owner.cache = cache
    monkeypatch.setattr(C, "census", _census)
    monkeypatch.setattr(C, "feed_event_view", lambda cache: {1: []})
    monkeypatch.setitem(FR._STATE, "owner", _Owner())
    from sportsassets import pinnapi_reactive as RX
    monkeypatch.setattr(RX, "ACTIVE", None)
    out = asyncio.run(FR._census_once(_Pool()))
    assert out["ok"] is True
    assert "venue_counterpart_fixture_ids" not in out
    assert out["venue_counterpart_by_state"] == {"MATCHED": 2}
    assert out["protected"]["venue_counterpart"] == 2
    assert out["protected"]["protected"] == 4
    assert cache.is_protected(1) and cache.is_protected(3)
    assert not cache.is_protected(2)
    assert where["thread"] != threading.current_thread().name
    # the heartbeat's cache section carries the retention block, bounded
    body = FR._capped({"beat_at": NOW, "cache": cache.census(),
                       "coverage_census": out})
    d = json.loads(body)
    assert d["cache"]["retention"]["protected_ids"] == 4
    assert d["coverage_census"]["protected"]["protected"] == 4
    assert "venue_counterpart_fixture_ids" not in json.dumps(d)
