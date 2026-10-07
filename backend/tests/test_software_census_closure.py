"""THE SOFTWARE FIRST-LOSS CENSUS, CLOSED CODE BY CODE ON RECORDED EVIDENCE.

PRODUCTION (live release 55eb7c82, 1 h census: SOFTWARE 96 / EXTERNAL 37 /
ECONOMIC 13). Each SOFTWARE code was read where it is emitted and either
FIXED in code or SPLIT so that the part which is not ours is named by the
evidence its emitter records -- never relabelled:

  QUOTE_STALE_ON_ARRIVAL (46 events)
      the row already measures provider_lag_s (our receipt - the provider's
      last_update) beside our_processing_s. Delivered past the 30 s rule by
      the metered provider while the PinnAPI read was refused for an
      EXTERNAL reason -> QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER (EXTERNAL),
      the WS refusal beside it. A quote our processing took past the limit,
      or one whose PinnAPI refusal is ours, stays QUOTE_STALE_ON_ARRIVAL.
  PINNAPI_PRIMARY_NO_EXACT_FIXTURE (16 events, every row with
  THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK)
      no feed record starting inside the match tolerance shares a token with
      either team AND the metered payload carries no Pinnacle book -> two
      sources agree Pinnacle has not posted the game:
      PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED (EXTERNAL), with the payload's
      absence and (when the feed names a team at other starts)
      PINNAPI_FEED_NAMES_THE_TEAMS_ONLY_AT_OTHER_START_TIMES beside it. Any
      token near the start keeps NO_EXACT_FIXTURE (a naming gap, ours).
  PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE (6 events)
      a wrapper: the decision now carries the lane's own probability codes
      behind it and the census classes the loss by the carried code.
  THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY (4 events)
      a read that SUCCEEDED on a valid, empty side bought is the market's
      own state: THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT (ECONOMIC
      / DEPTH); a failed or malformed read keeps the old code.

No freshness limit, tolerance, name table, gate or threshold changes; the
tests below pin that too.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import time
import types

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import coverage_first_loss as CFL
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_names as N
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

T = _dt.datetime(2026, 10, 14, 23, 30, tzinfo=_dt.timezone.utc).timestamp()
LIMIT = loop.PINNACLE_MAX_AGE_S


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat() \
        .replace("+00:00", "Z")


# ═════════════════════════════════════════════════════════════════════
# 0 · NOTHING MOVED: the limits, the tolerance, the classes
# ═════════════════════════════════════════════════════════════════════

def test_no_limit_or_tolerance_moved():
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert P.START_TOLERANCE_S == 90 * 60
    assert N.ABSENCE_WINDOW_S == 36 * 3600
    # NO_EXACT_FIXTURE itself is still ours
    assert CFL.classify(P.R_NO_EXACT)["class"] == RT.SOFTWARE
    assert CFL.classify(loop.R_QUOTE_STALE_ON_ARRIVAL)["class"] == \
        RT.SOFTWARE


@pytest.mark.parametrize("code,cls,lane", (
    ("QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER", CFL.EXTERNAL,
     "2_FRESHNESS"),
    ("PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED", CFL.EXTERNAL,
     "1_PROBABILITY"),
    ("PINNAPI_FEED_NAMES_THE_TEAMS_ONLY_AT_OTHER_START_TIMES", CFL.EXTERNAL,
     "1_PROBABILITY"),
))
def test_every_new_code_is_classified_staged_and_external(code, cls, lane):
    assert RT.classify(code)["classified"], code
    assert ext.EVALUABILITY_OF[code] == ext.EXTERNAL_DEPENDENCY
    assert CFL.classify(code)["class"] == cls
    assert ext.STAGE_OF[code] == lane


def test_the_empty_side_code_is_economic_depth():
    k = RT.classify(PD.R_SIDE_EMPTY_ON_A_READ_BOOK)
    assert k["classified"] and k["class"] == RT.ECONOMIC
    assert k["family"] == "DEPTH"
    assert CFL.classify(PD.R_SIDE_EMPTY_ON_A_READ_BOOK)["class"] == \
        RT.ECONOMIC
    # the read-failure code is still SOFTWARE
    assert CFL.classify(PD.R_NO_BOOK)["class"] == RT.SOFTWARE


# ═════════════════════════════════════════════════════════════════════
# 1 · QUOTE_STALE_ON_ARRIVAL: WHOSE AGE, BY THE ROW'S OWN MEASUREMENT
# ═════════════════════════════════════════════════════════════════════

def _metered(ws_reason):
    return {"home": "Arkansas Razorbacks", "away": "Tennessee Volunteers",
            "reference_input": {"provider": P.LEGACY_PROVIDER,
                                "preferred_provider": P.PROVIDER,
                                "fallback_reason": ws_reason}}


def test_quote_stale_delivered_by_the_provider_with_an_external_ws_refusal():
    pe = 1000.0
    got = loop.stale_on_arrival_attribution(
        _metered(N.R_NOT_IN_FEED), provider_epoch=pe, received_at=pe + 45.0)
    assert got["code"] == loop.R_QUOTE_STALE_AS_DELIVERED
    assert got["ws_refusal"] == N.R_NOT_IN_FEED
    assert got["provider_lag_s"] == 45.0
    assert got["basis"].startswith("PROVIDER_LAG_AT_RECEIPT_PAST_THE_LIMIT")


def test_quote_stale_delivered_stale_but_the_ws_refusal_is_ours_stays_ours():
    pe = 1000.0
    for ours in (P.R_NO_EXACT, F.R_NO_AUTHORITY,
                 "PINNAPI_PRIMARY_SPORT_UNSUPPORTED", None):
        got = loop.stale_on_arrival_attribution(
            _metered(ours), provider_epoch=pe, received_at=pe + 45.0)
        assert got["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL, ours
        assert got["ws_refusal"] == ours


def test_quote_stale_our_processing_took_it_past_stays_ours():
    pe = 1000.0
    got = loop.stale_on_arrival_attribution(
        _metered(N.R_NOT_IN_FEED), provider_epoch=pe, received_at=pe + 15.0)
    assert got["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL
    assert got["basis"] == \
        "DELIVERED_INSIDE_THE_LIMIT_OUR_PROCESSING_TOOK_IT_PAST"
    # exactly at the limit is inside it (the rule is `> limit`)
    got = loop.stale_on_arrival_attribution(
        _metered(N.R_NOT_IN_FEED), provider_epoch=pe,
        received_at=pe + LIMIT)
    assert got["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL


def test_quote_stale_a_pinnapi_quote_or_an_unmeasured_lag_is_never_external():
    pe = 1000.0
    ws = {"reference_input": {"provider": P.PROVIDER}}
    got = loop.stale_on_arrival_attribution(ws, provider_epoch=pe,
                                            received_at=pe + 45.0)
    assert got["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL
    assert got["ws_refusal"] is None
    got = loop.stale_on_arrival_attribution(
        _metered(N.R_NOT_IN_FEED), provider_epoch=pe, received_at=None)
    assert got["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL
    assert got["basis"] == "PROVIDER_LAG_UNMEASURED"


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


def _odds_event(Fx, *, stamp_epoch):
    stamp = _dt.datetime.fromtimestamp(stamp_epoch, _dt.timezone.utc
                                       ).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = Fx.odds_event(time.time())
    for bk in ev["bookmakers"]:
        bk["last_update"] = stamp
        for m in bk["markets"]:
            if "last_update" in m:
                m["last_update"] = stamp
    return ev


async def _cycle_with(monkeypatch, *, received_off, stamp_off, ws_reason):
    """The REAL scheduled cycle over the production-shaped single-event
    fixture, the provider's HTTP substituted, and the PinnAPI read refusing
    with `ws_reason` (the metered quote is its fallback)."""
    from tests import _emptybook_fixture as Fx
    conn = await _connect()
    venue = Fx.Venue()
    await Fx.clean(conn)
    await Fx.seed(conn)
    real_currency = loop.book_currency_evidence
    Fx.substitute(monkeypatch, venue)
    monkeypatch.setattr(loop, "book_currency_evidence", real_currency)
    from sportsassets import bettor_entry_execution as EX
    from sportsassets import bettor_funded_execution as FX
    from sportsassets import bettor_funded_management as FM
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", False)

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": now,
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True,
                "events": [_odds_event(Fx, stamp_epoch=now + stamp_off)],
                "received_at": now + received_off,
                "credits_used": "1", "credits_remaining": "9"}
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    real_primary = loop.primary_pinnacle_h2h

    def primary(event, *, received_at, family, at, explain=None):
        q = real_primary(event, received_at=received_at, family=family,
                         at=at, explain=explain)
        if q is None:
            return None
        q = dict(q)
        q["reference_input"] = {"provider": P.LEGACY_PROVIDER,
                                "preferred_provider": P.PROVIDER,
                                "fallback_reason": ws_reason}
        if isinstance(explain, dict):
            explain["reason"] = ws_reason
        return q
    monkeypatch.setattr(loop, "primary_pinnacle_h2h", primary)
    handed: list = []

    async def paper_hook(conn_, valuation_id):
        handed.append(valuation_id)
    monkeypatch.setattr(loop, "_paper_valuation", paper_hook)
    out = await loop.cycle(conn)
    rows = await conn.fetch(
        "SELECT first_refusal, codes, provider_lag_s, our_processing_s "
        "  FROM ext_candidate_outcomes "
        " WHERE provider_event_id LIKE 'odds-emptybook%' ORDER BY id")
    return conn, Fx, venue, out, rows, handed


@pg
@pytest.mark.asyncio
async def test_quote_stale_as_delivered_is_written_on_the_row_with_its_lag(
        monkeypatch):
    """45 s old at our receipt, PinnAPI refused FEED_HOLDS_NO_FIXTURE: the
    row is QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER, its measured lag on the
    row past the limit, the WS refusal beside it; nothing was handed on."""
    conn, Fx, venue, out, rows, handed = await _cycle_with(
        monkeypatch, received_off=0.0, stamp_off=-45.0,
        ws_reason=N.R_NOT_IN_FEED)
    try:
        assert out["ran"] is True, out.get("why")
        assert out["refusals"].get(loop.R_QUOTE_STALE_AS_DELIVERED) == 1
        assert not out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL)
        assert out["latency"]["stale_on_arrival_by_attribution"] == {
            loop.R_QUOTE_STALE_AS_DELIVERED: 1}
        assert len(rows) == 1
        r = rows[0]
        assert r["first_refusal"] == loop.R_QUOTE_STALE_AS_DELIVERED
        codes = json.loads(r["codes"]) if isinstance(r["codes"], str) \
            else list(r["codes"])
        assert codes[:2] == [loop.R_QUOTE_STALE_AS_DELIVERED, N.R_NOT_IN_FEED]
        assert r["provider_lag_s"] > LIMIT       # the evidence, on the row
        # the census classes the row EXTERNAL by its code
        fl = CFL.first_loss_of_event(
            {"first_refusal": r["first_refusal"], "codes": codes,
             "outcome": "REFUSED", "stage": "2_FRESHNESS", "reach": 2},
            [], [], valuations_read=True, decisions_read=True)
        assert fl["class"] == CFL.EXTERNAL
        assert handed == [] and venue.creates_sent() == []
    finally:
        await Fx.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_quote_stale_our_delay_or_our_ws_refusal_stays_software(
        monkeypatch):
    """The same 45 s quote whose PinnAPI refusal is OURS (a naming gap):
    still QUOTE_STALE_ON_ARRIVAL, the WS reason beside it on the row."""
    conn, Fx, venue, out, rows, handed = await _cycle_with(
        monkeypatch, received_off=0.0, stamp_off=-45.0,
        ws_reason=P.R_NO_EXACT)
    try:
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert not out["refusals"].get(loop.R_QUOTE_STALE_AS_DELIVERED)
        r = rows[0]
        codes = json.loads(r["codes"]) if isinstance(r["codes"], str) \
            else list(r["codes"])
        assert r["first_refusal"] == loop.R_QUOTE_STALE_ON_ARRIVAL
        assert codes[:2] == [loop.R_QUOTE_STALE_ON_ARRIVAL, P.R_NO_EXACT]
        assert CFL.classify(r["first_refusal"])["class"] == RT.SOFTWARE
    finally:
        await Fx.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · NO_EXACT_FIXTURE: PINNACLE HAS NOT POSTED IT -- TWO SOURCES AGREE
# ═════════════════════════════════════════════════════════════════════

def _feed(fixtures, *, sport_id=1, at=None):
    """A granted, synced FeedCache holding `fixtures` [(id, start, home,
    away)] of one sport in one prematch snapshot."""
    at = T - 4 * 86400 if at is None else at
    c = F.FeedCache()
    e = c.new_connection([("prematch", sport_id)])
    evs = [{"id": fid, "startTime": iso(start), "isLive": False,
            "participants": [{"name": h, "alignment": "home"},
                             {"name": w, "alignment": "away"}],
            "markets": []}
           for fid, start, h, w in fixtures]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": sport_id,
             "ts": (at - 60) * 1000, "events": evs}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    c.authority = types.SimpleNamespace(granted=True, synced=True,
                                        epoch=c.authority.epoch, reason=None)
    return c


def _mls(home="D.C. United", away="New York Red Bulls", *, books=None):
    """The metered provider's MLS event, as production carried it: no
    Pinnacle book (only another bookmaker, or none)."""
    return {"id": "0e8e2e026e81bda29595d19cfb22050e",
            "sport_key": "soccer_usa_mls", "home_team": home,
            "away_team": away, "commence_time": iso(T),
            "bookmakers": ([{"key": "draftkings", "markets": []}]
                           if books is None else books)}


def _select(cache, event):
    why: dict = {}
    got = P.select(cache, event, loop.pinnacle_h2h(event, received_at=T),
                   family="soccer", sharp_books=set(loop.SHARP_BOOKS),
                   at=T - 4 * 86400, runtime_id="r1", explain=why)
    return got, why


def test_not_yet_posted_when_the_feed_names_the_teams_only_at_other_starts():
    """Production shape: D.C. United v NY Red Bulls 2026-10-14, 8 days
    out. Inside the wide 36 h window the feed names a participant TOKEN at
    other starts (another club's "United" the evening before, "New York"
    City five hours later -- which kept the 36 h absence check from firing)
    and nothing inside the match tolerance; the metered payload has no
    Pinnacle book."""
    c = _feed([(1, T - 20 * 3600, "Atlanta United", "Orlando City"),
               (2, T + 5 * 3600, "New York City", "CF Montreal"),
               (3, T, "Flamengo", "Palmeiras")])
    ev = _mls()
    got, why = _select(c, ev)
    assert got is None and why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is True
    assert ab["named_near_start"] == []
    assert len(ab["named_elsewhere"]) == 2
    codes = loop.no_pinnacle_codes(why, ev)
    assert codes == [loop.R_FIXTURE_NOT_YET_POSTED,
                     loop.R_PAYLOAD_HAS_NO_PINNACLE,
                     loop.R_TEAMS_ONLY_AT_OTHER_STARTS]
    ev_ = loop.fixture_not_yet_posted(why, ev)
    assert ev_["payload_absence"] == loop.R_PAYLOAD_HAS_NO_PINNACLE
    assert ev_["near_start_window_s"] == P.START_TOLERANCE_S
    # staged and classed by its first code, as the ledger row is
    assert loop.no_pinnacle_stage(codes) == "1_PROBABILITY"
    assert CFL.classify(codes[0])["class"] == CFL.EXTERNAL


def test_not_yet_posted_without_any_other_record_of_the_teams():
    """The feed holds soccer, none of it near the start or naming either
    club inside the tolerance: still NOT_YET_POSTED, no evidence code for
    other starts (the 36 h absence check may or may not have fired)."""
    c = _feed([(3, T + 3 * 86400, "Flamengo", "Palmeiras")])
    ev = _mls("Botafogo-SP", "Ceará")
    got, why = _select(c, ev)
    codes = loop.no_pinnacle_codes(why, ev)
    if why["reason"] == N.R_NOT_IN_FEED:
        assert codes[0] == N.R_NOT_IN_FEED      # the older EXTERNAL code
    else:
        assert codes == [loop.R_FIXTURE_NOT_YET_POSTED,
                         loop.R_PAYLOAD_HAS_NO_PINNACLE]


def test_a_token_near_the_start_keeps_the_naming_gap_ours():
    """The feed holds a record AT the start naming one club differently
    ("DC Utd"): it could be this fixture under other names -- NO_EXACT
    stays, SOFTWARE."""
    c = _feed([(4, T + 600, "DC Utd", "NY RB")])
    ev = _mls()
    got, why = _select(c, ev)
    assert why["reason"] == P.R_NO_EXACT
    ab = why["provenance"]["fixture_match"]["absence"]
    assert ab["no_candidate_near_start"] is False
    assert ab["named_near_start"]
    codes = loop.no_pinnacle_codes(why, ev)
    assert codes[0] == P.R_NO_EXACT
    assert CFL.classify(codes[0])["class"] == RT.SOFTWARE


def test_a_pinnacle_book_in_the_payload_or_a_seed_or_an_eviction_stays_ours():
    c = _feed([(1, T - 20 * 3600, "Atlanta United", "Orlando City"),
               (3, T, "Flamengo", "Palmeiras")])
    # the metered payload CARRIES a Pinnacle book: Pinnacle has posted it,
    # so the feed's miss is ours (the quote becomes the fallback)
    pin = [{"key": "pinnacle", "last_update": iso(T - 4 * 86400 - 5),
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "D.C. United", "price": 2.4},
                {"name": "New York Red Bulls", "price": 2.9},
                {"name": "Draw", "price": 3.4}]}]}]
    ev = _mls(books=pin)
    got, why = _select(c, ev)
    assert got is not None              # the metered fallback
    assert loop.fixture_not_yet_posted(why, ev) is None
    # a PinnAPI-native seed is never called unposted
    seed = dict(_mls(), pinnapi_native={"fixture_id": 9})
    got, why = _select(c, seed)
    assert loop.fixture_not_yet_posted(why, seed) is None
    assert loop.no_pinnacle_codes(why, seed)[0] != \
        loop.R_FIXTURE_NOT_YET_POSTED
    # an eviction: the fixture may have been ours to keep
    c.counts["events_evicted"] = 1
    ev = _mls()
    got, why = _select(c, ev)
    assert loop.fixture_not_yet_posted(why, ev) is None
    assert loop.no_pinnacle_codes(why, ev)[0] == P.R_NO_EXACT


def test_a_matching_fixture_is_still_matched_canonically():
    """The scan decides only a MISS: a feed fixture whose names differ but
    canonicalise ("Vancouver Whitecaps" / "Vancouver Whitecaps FC") is
    matched, as before."""
    c = _feed([(5, T, "Los Angeles FC", "Vancouver Whitecaps")])
    hit, why = P.match_event(
        c, {"home_team": "Los Angeles FC",
            "away_team": "Vancouver Whitecaps FC", "commence_time": iso(T),
            "sport_key": "soccer_usa_mls"}, "soccer", explain={})
    assert why is None and hit[0] == 5


def test_the_absence_scan_is_pure_and_bounded():
    recs = [{"sport_id": 1, "startTime": iso(T - 86400 * k),
             "participants": [{"name": "DC United"}, {"name": "X%d" % k}]}
            for k in range(1, 10)]
    ab = N.absence(recs, sport_id=1, start=T, home="D.C. United",
                   away="New York Red Bulls", family="soccer",
                   tolerance_s=P.START_TOLERANCE_S)
    assert ab["no_candidate_near_start"] is True
    assert len(ab["named_elsewhere"]) == N.NEAR_START_SAMPLE
    # a record with no readable start counts as near (any doubt is ours)
    recs.append({"sport_id": 1, "startTime": None,
                 "participants": [{"name": "DC United"}, {"name": "Y"}]})
    ab = N.absence(recs, sport_id=1, start=T, home="D.C. United",
                   away="New York Red Bulls", family="soccer",
                   tolerance_s=P.START_TOLERANCE_S)
    assert ab["no_candidate_near_start"] is False
    # without a tolerance the old answer is unchanged
    assert "no_candidate_near_start" not in N.absence(
        recs, sport_id=1, start=T, home="a", away="b", family="soccer")


# ═════════════════════════════════════════════════════════════════════
# 3 · PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE: THE CARRIED CODE
# ═════════════════════════════════════════════════════════════════════

def test_the_lane_codes_ride_behind_the_wrapper_on_the_decision():
    cand = {"refusals": ["THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK",
                         "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"],
            "us_market_slug": "aec-cfb-tenn-ark-2026-10-10",
            "payout_event": "Arkansas Razorbacks", "period": "FULL_GAME",
            "fixture": {"id": 1}, "side": PD.DP.LONG,
            "settlement": {"compatibility": "COMPATIBLE",
                           "overall_established": True}}
    row = {"contract_selection": "Arkansas Razorbacks",
           "payout_event": "Arkansas Razorbacks",
           "probability_event": "Arkansas Razorbacks"}
    m = PB.contract_match(cand, row)
    refs = m["refusals"]
    i = refs.index(PB.R_PROBABILITY_UNQUALIFIED)
    assert refs[i + 1] == "THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK"
    # a freshness code (not probability-stage) is not carried
    assert "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED" not in refs


def test_the_census_classes_the_wrapper_by_its_carried_code():
    dec = {"verdict": "REFUSE",
           "refusals": [PB.R_PROBABILITY_UNQUALIFIED,
                        "THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK"]}
    fl = CFL.first_loss_of_event({}, [{"refusals": []}], [dec],
                                 valuations_read=True, decisions_read=True)
    assert fl["code"] == "THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK"
    assert fl["class"] == CFL.EXTERNAL
    assert fl["carried_by"] == PB.R_PROBABILITY_UNQUALIFIED
    # a SOFTWARE carried code stays SOFTWARE, by its own name
    dec = {"verdict": "REFUSE",
           "refusals": [PB.R_PROBABILITY_UNQUALIFIED, ext.R_THIN_OUTCOME]}
    fl = CFL.first_loss_of_event({}, [{"refusals": []}], [dec],
                                 valuations_read=True, decisions_read=True)
    assert fl["code"] == ext.R_THIN_OUTCOME
    assert fl["class"] == RT.SOFTWARE
    # a decision written before the codes were carried keeps the wrapper
    dec = {"verdict": "REFUSE", "refusals": [PB.R_PROBABILITY_UNQUALIFIED]}
    fl = CFL.first_loss_of_event({}, [{"refusals": []}], [dec],
                                 valuations_read=True, decisions_read=True)
    assert fl["code"] == PB.R_PROBABILITY_UNQUALIFIED
    assert fl["class"] == RT.SOFTWARE and "carried_by" not in fl


# ═════════════════════════════════════════════════════════════════════
# 4 · THE OBSERVED BOOK: A VALID EMPTY SIDE IS THE MARKET'S STATE
# ═════════════════════════════════════════════════════════════════════

def _obs(md, error=None):
    return {"market_data": md, "error": error}


def test_a_read_book_with_an_empty_side_bought_is_economic():
    md = {"bids": [{"px": {"value": "0.40"}, "qty": "10"}], "offers": []}
    lv = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    assert lv["levels"] == [] and lv["book_was"] == "VALID_BUT_EMPTY"
    assert PD.no_book_refusal(_obs(md), lv) == PD.R_SIDE_EMPTY_ON_A_READ_BOOK


def test_a_failed_malformed_or_absent_side_keeps_the_unreadable_code():
    # a failed read: no book at all
    lv = SIM.levels_for(None, direction="BUY", holding_side="LONG")
    assert PD.no_book_refusal(_obs(None, error="VENUE_TIMEOUT"), lv) == \
        PD.R_NO_BOOK
    # the side absent from the payload
    md = {"bids": []}
    lv = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    assert PD.no_book_refusal(_obs(md), lv) == PD.R_NO_BOOK
    # a malformed level
    md = {"offers": [{"px": {"value": "abc"}, "qty": "x"}]}
    lv = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    assert PD.no_book_refusal(_obs(md), lv) == PD.R_NO_BOOK
    # an error on the observation always wins
    md = {"offers": []}
    lv = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    assert PD.no_book_refusal(_obs(md, error="X"), lv) == PD.R_NO_BOOK
    # levels our cent grid excluded are ours
    lv = dict(lv, excluded_off_cent_grid=1)
    assert PD.no_book_refusal(_obs(md), lv) == PD.R_NO_BOOK


def test_every_paper_entry_site_uses_the_split():
    import inspect
    from sportsassets.agents import paper_explore as PEX
    from sportsassets.agents import paper_maker as PMK
    for mod in (PD, PB, PEX, PMK):
        src = inspect.getsource(mod)
        assert "no_book_refusal(obs, lv)" in src, mod.__name__
        assert "or not levels:\n            refusals.append(R_NO_BOOK)" \
            not in src
        assert "or not levels:\n            refusals.append(PB.R_NO_BOOK)" \
            not in src
