"""QL-1: A FROZEN FEED IS DISTINGUISHABLE FROM A QUIET LINE -- EVIDENCE ONLY.

PRODUCTION (RC6.3b root-cause audit, research-sql runs 38055963121 ..
38057645735): 33 of the 38 QUOTE_STALE_ON_ARRIVAL first-loss events of the
approved-judge packet window carried FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE
or FEED_QUOTE_OLDER_THAN_LIMIT beside them, and one more event's first loss
was FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE itself (34 events). Nothing on
those rows said whether the provider was still asserting the price (a quiet
line, re-sent by every authoritative list) or had stopped talking about the
market (a frozen feed) -- the question C1 has to answer before it chooses
whether an entry may read a provider confirmation.

WHAT THIS PINS (no policy change):
  * `pinnapi_feed.read` puts three measured facts on every R_NO_CHANGE_TIME /
    R_STALE refusal: `would_pass_on_confirmation` (the held-read rule's own
    answer), `no_observed_change_s` (basis named) and
    `age_since_confirmation_s`; it refuses exactly as before and an admitted
    read carries none of it;
  * `would_pass_on_confirmation` agrees with `read_held` on every case, so
    the evidence can never say something the held read would not do;
  * `pinnapi_primary.select` carries the provenance on the explain and on the
    fallback, and `quiet_line_evidence` reads it back by name (never from a
    record written before the fields existed);
  * the collector writes ONE evidence code beside the recorded freshness
    refusal on the ledger row (`ext_candidate_outcomes.codes`), head = the
    answer, detail = the numbers; it is never a first refusal, the first-loss
    census drops it, and the taxonomy declares it NOT a refusal;
  * on real Postgres, through the collector's own writer and through a whole
    cycle, the row carries the fields and the readback SQL reads them.

The entry switch (PINNAPI_ENTRY_ON_CONFIRMATION, `read_entry`) is NOT built.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import uuid

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import coverage_first_loss as CFL
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

LIMIT = 30.0
ML = {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
      "prices": [{"designation": "home", "price": -120},
                 {"designation": "away", "price": 105}]}
T0 = 1_000_000                      # ms: the subscribe snapshot's stamp
HOURS = 3 * 3_600_000               # ms: "unchanged for hours"


def _cache(ts=T0):
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 5)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": ts, "events": [{"id": 9, "version": 7, "markets": [ML]}]},
            epoch=ep, received_ms=ts + 20)
    return c, ep


def _markets(c, ep, ts, *, prices=None, stamped=True):
    """An authoritative prematch_markets list re-asserting (or moving) the
    price; `stamped=False` sends it without a provider stamp."""
    m = dict(ML, version=7)
    if prices is not None:
        m = dict(m, prices=prices)
    frame = {"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "data": [m]}
    if stamped:
        frame["ts"] = ts
    c.apply(frame, epoch=ep, received_ms=ts + 30)


def _quiet_for_hours(c, ep, *, until, every=300_000):
    """Provider-stamped re-assertions of the SAME price every `every` ms up
    to `until` (the last one exactly at `until`)."""
    ts = T0 + 60_000
    while ts < until:
        _markets(c, ep, ts)
        ts += every
    _markets(c, ep, until)


MOVED = [{"designation": "home", "price": -115},
         {"designation": "away", "price": 100}]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FEED: three facts on every change-rule refusal
# ═════════════════════════════════════════════════════════════════════

def test_a_refusal_for_a_quote_confirmed_10s_ago_and_unchanged_for_hours_carries_the_three_fields():
    """THE REGRESSION. A money line first seen in the subscribe snapshot,
    re-asserted by provider-stamped authoritative lists for three hours, the
    last one 10 s before the read: `read` still refuses
    FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE (no change observed, the 30 s
    rule unchanged) -- and the provenance now says it would have passed on
    confirmation, has shown no change for ~3 h, and was confirmed 10 s ago.
    On the base tree the three keys are absent."""
    c, ep = _cache()
    ev = T0 + HOURS
    _quiet_for_hours(c, ep, until=ev - 10_000)
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert r["ok"] is False and r["reason"] == F.R_NO_CHANGE_TIME
    p = r["provenance"]
    for k in F.QUIET_LINE_FIELDS:
        assert k in p, k
    assert p["would_pass_on_confirmation"] is True
    assert p["age_since_confirmation_s"] == 10.0
    # no change was ever observed: measured from our first receipt of this
    # price on this connection (the snapshot arrived at T0 + 20 ms)
    assert p["no_observed_change_s"] == pytest.approx((ev - (T0 + 20)) / 1000)
    assert p["no_observed_change_s"] > 2 * 3600
    assert p["no_observed_change_basis"] == F.NO_CHANGE_SINCE_FIRST_OBSERVED
    assert p["confirmation_limit_s"] == LIMIT
    # the evidence is measured, never decided on: the change rule still
    # refuses and says so
    assert p["freshness_basis"] == F.FRESHNESS_BASIS
    assert p["confirmation_is_a_decision_input"] is False
    assert p["change_ms"] is None, "the change clock is never invented"
    # ...and the held read, whose rule the evidence names, agrees
    h = c.read_held(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert h["ok"] is True and h["provenance"]["quote_age_s"] == 10.0


def test_a_stale_change_reconfirmed_by_the_provider_measures_from_the_last_change():
    """A price that MOVED hours ago and has been re-asserted since: R_STALE,
    `no_observed_change_s` is the quote's own age from that change (the
    basis says so), confirmed 10 s ago, would pass on confirmation."""
    c, ep = _cache()
    moved_at = T0 + 600_000
    _markets(c, ep, moved_at, prices=MOVED)
    ev = moved_at + HOURS
    ts = moved_at + 300_000
    while ts < ev - 10_000:
        _markets(c, ep, ts, prices=MOVED)
        ts += 300_000
    _markets(c, ep, ev - 10_000, prices=MOVED)
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert r["ok"] is False and r["reason"] == F.R_STALE
    p = r["provenance"]
    assert p["quote_age_s"] == pytest.approx(HOURS / 1000)
    assert p["no_observed_change_s"] == p["quote_age_s"]
    assert p["no_observed_change_basis"] == F.NO_CHANGE_SINCE_LAST_CHANGE
    assert p["age_since_confirmation_s"] == 10.0
    assert p["would_pass_on_confirmation"] is True
    assert p["change_ms"] == moved_at and p["freshness_at_ms"] == moved_at
    assert c.read_held(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)["ok"]


@pytest.mark.parametrize("shape", [
    "never_confirmed", "confirmation_31s_old", "confirmation_dated_locally",
    "resubscribe_snapshot_only"])
def test_a_frozen_feed_would_not_pass_on_confirmation(shape):
    """The other answer, by name: no confirmation at all, the latest one
    older than the limit, one dated only by our receipt, or only a
    re-subscribe snapshot (the provider's stored mirror) -- the refusal
    carries would_pass_on_confirmation False and the held read refuses the
    same way."""
    c, ep = _cache()
    ev = T0 + HOURS
    if shape == "never_confirmed":
        pass
    elif shape == "confirmation_31s_old":
        _quiet_for_hours(c, ep, until=ev - 31_000)
    elif shape == "confirmation_dated_locally":
        _quiet_for_hours(c, ep, until=ev - 120_000)
        _markets(c, ep, ev - 10_000, stamped=False)
    else:
        c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
                 "ts": ev - 10_000,
                 "events": [{"id": 9, "version": 7, "markets": [ML]}]},
                epoch=ep, received_ms=ev - 9_980)
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert r["ok"] is False and r["reason"] == F.R_NO_CHANGE_TIME
    p = r["provenance"]
    assert p["would_pass_on_confirmation"] is False
    assert p["no_observed_change_s"] == pytest.approx((ev - (T0 + 20)) / 1000)
    if shape == "never_confirmed":
        assert p["age_since_confirmation_s"] is None
        assert p["confirmed_ms"] is None
    elif shape == "confirmation_31s_old":
        assert p["age_since_confirmation_s"] == 31.0
    elif shape == "confirmation_dated_locally":
        # measured from OUR receipt of the unstamped list, and named so
        assert p["age_since_confirmation_s"] == pytest.approx(9.97)
        assert p["confirmed_clock"] == F.CLOCK_LOCAL
    else:
        assert p["age_since_confirmation_s"] == 10.0
        assert p["confirmed_by"] == F.C_SNAPSHOT_RECONFIRM
    h = c.read_held(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert h["ok"] is False and h["reason"] == F.R_NO_CHANGE_TIME


def _confirmed_quote(*, age_s, kind, clock):
    q = F.Quote(key="s;0;m", event_id=9, sport_id=5, stream="prematch",
                period=0, market_type="moneyline", side=None, line=None,
                prices={"home": -120, "away": 105}, epoch=1,
                source_change_ms=None, frame_ts_ms=T0, received_ms=T0 + 20,
                first_observed_ms=T0 + 20)
    if kind is not None:
        q.confirmed_ms = T0 + HOURS - age_s * 1000.0
        q.confirmed_received_ms = q.confirmed_ms + 30
        q.confirmed_by, q.confirmed_clock = kind, clock
    return q


@pytest.mark.parametrize("age_s", [-5.0, 0.0, 10.0, 30.0, 30.001, 61.2])
@pytest.mark.parametrize("kind", [F.C_LIVE_REC, F.C_PREMATCH_MARKETS,
                                  F.C_MATCHUP_VERSION, F.C_SNAPSHOT_RECONFIRM,
                                  None])
@pytest.mark.parametrize("clock", [F.CLOCK_PROVIDER, F.CLOCK_LOCAL])
def test_the_evidence_agrees_with_the_held_read_on_every_case_and_read_refuses_identically(
        age_s, kind, clock):
    """Over every confirmation kind x clock x age (inside, at and past the
    limit, in the future, never): `would_pass_on_confirmation` equals what
    `read_held` does, `read` refuses exactly as the change rule says, and
    the ONLY cases that pass are a provider-stamped admitted kind within
    [0, limit] -- the subscribe snapshot and a locally dated confirmation
    never do."""
    c, ep = _cache()
    q = _confirmed_quote(age_s=age_s, kind=kind, clock=clock)
    q.epoch = ep
    c.quotes[(9, "s;0;m")] = q
    ev = T0 + HOURS
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    h = c.read_held(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    assert r["ok"] is False and r["reason"] == F.R_NO_CHANGE_TIME
    assert r["provenance"]["would_pass_on_confirmation"] == h["ok"]
    assert F.confirmation_admits(q, ev, LIMIT) == h["ok"]
    expected = (kind in F.PROVIDER_CONFIRMATIONS and clock == F.CLOCK_PROVIDER
                and 0 <= age_s <= LIMIT)
    assert h["ok"] is expected, (age_s, kind, clock)
    if kind is None:
        assert r["provenance"]["age_since_confirmation_s"] is None
    else:
        assert r["provenance"]["age_since_confirmation_s"] == \
            pytest.approx(age_s, abs=1e-3)


def test_an_admitted_read_and_the_other_refusals_carry_no_quiet_line_evidence():
    """The evidence belongs to the two change-rule refusals only: an ok
    read, a change in the future, an unknown market, a closed market and a
    quote from an older connection carry none of it (nothing is inferred
    about a price the rule did not refuse for its age)."""
    c, ep = _cache()
    _markets(c, ep, T0 + 100_000, prices=MOVED)
    ok = c.read(9, "s;0;m", evaluated_ms=T0 + 110_000, max_age_s=LIMIT)
    assert ok["ok"] is True
    assert not any(k in ok["provenance"] for k in F.QUIET_LINE_FIELDS
                   if k != "age_since_confirmation_s")
    assert "would_pass_on_confirmation" not in ok["provenance"]
    fut = c.read(9, "s;0;m", evaluated_ms=T0 + 90_000, max_age_s=LIMIT)
    assert fut["reason"] == F.R_FUTURE
    assert "would_pass_on_confirmation" not in fut["provenance"]
    assert c.read(9, "nope")["reason"] == F.R_UNKNOWN_MARKET
    assert "provenance" not in c.read(9, "nope")
    c.new_connection([("prematch", 5)])
    old = c.read(9, "s;0;m", evaluated_ms=T0 + 110_000)
    assert old["ok"] is False and "provenance" not in old


# ═════════════════════════════════════════════════════════════════════
# 2 · THE SELECTION: the provenance travels; the evidence is read by name
# ═════════════════════════════════════════════════════════════════════

AT = 1_791_050_000.0


def _iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat()


def _fixture_cache():
    """A baseball fixture whose money line was first seen in the snapshot
    and re-asserted by provider-stamped lists, the last 10 s before AT."""
    teams = ("Atlanta Braves", "Los Angeles Dodgers")
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open",
              "prices": [{"designation": "home", "price": -125},
                         {"designation": "away", "price": 110}]}
    event = {"id": 123, "startTime": _iso(AT + 3600), "isLive": False,
             "participants": [{"name": teams[0], "alignment": "home"},
                              {"name": teams[1], "alignment": "away"}],
             "markets": [market]}
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 6)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 6,
             "ts": (AT - 7200) * 1000, "events": [event]}, epoch=ep,
            received_ms=(AT - 7200) * 1000 + 5)
    for off in (-3600, -1800, -600, -10):
        c.apply({"type": "prematch_markets", "sport_id": 6, "matchup_id": 123,
                 "ts": (AT + off) * 1000, "data": [dict(market, version=1)]},
                epoch=ep, received_ms=(AT + off) * 1000 + 5)
    original = {"id": "discovery-123", "home_team": teams[0],
                "away_team": teams[1], "commence_time": _iso(AT + 3600),
                "bookmakers": [{"key": "pinnacle", "markets": [
                    {"key": "h2h", "last_update": _iso(AT - 5),
                     "outcomes": [{"name": teams[0], "price": 1.5},
                                  {"name": teams[1], "price": 2.5}]}]}]}
    return c, original


def test_select_carries_the_evidence_on_the_explain_and_on_the_fallback():
    c, ev = _fixture_cache()
    fallback = {"prices": {"Atlanta Braves": 1.5, "Los Angeles Dodgers": 2.5},
                "observed_at": AT - 5, "received_at": AT - 4}
    ex: dict = {}
    got = P.select(c, ev, fallback, family="baseball",
                   sharp_books={"pinnacle"}, at=AT, max_age_s=LIMIT,
                   runtime_id="r1", explain=ex)
    assert ex["reason"] == F.R_NO_CHANGE_TIME
    for prov in (ex["provenance"], got["reference_input"]["feed_read"]):
        assert prov["would_pass_on_confirmation"] is True
        assert prov["age_since_confirmation_s"] == 10.0
        assert prov["no_observed_change_s"] == pytest.approx(7200 - .005)
    assert got["reference_input"]["fallback_reason"] == F.R_NO_CHANGE_TIME
    # ...and read back by name
    e = P.quiet_line_evidence(ex["reason"], ex["provenance"])
    assert e == {"would_pass_on_confirmation": True,
                 "no_observed_change_s": ex["provenance"]["no_observed_change_s"],
                 "age_since_confirmation_s": 10.0,
                 "no_observed_change_basis": F.NO_CHANGE_SINCE_FIRST_OBSERVED,
                 "confirmation_limit_s": LIMIT}
    # no fallback: the explain alone carries it
    ex2: dict = {}
    assert P.select(c, ev, None, family="baseball", sharp_books={"pinnacle"},
                    at=AT, max_age_s=LIMIT, runtime_id="r1",
                    explain=ex2) is None
    assert P.quiet_line_evidence(ex2["reason"], ex2["provenance"]) == e


def test_quiet_line_evidence_is_read_only_off_a_change_rule_refusal_that_carries_it():
    prov = {"would_pass_on_confirmation": False, "no_observed_change_s": 61.2,
            "age_since_confirmation_s": None,
            "no_observed_change_basis": F.NO_CHANGE_SINCE_LAST_CHANGE,
            "confirmation_limit_s": 30.0, "confirmed_ms": None}
    assert P.quiet_line_evidence(F.R_STALE, prov)["would_pass_on_confirmation"] \
        is False
    # another reason, however complete the provenance
    assert P.quiet_line_evidence(P.R_NO_EXACT, prov) is None
    assert P.quiet_line_evidence(F.R_FUTURE, prov) is None
    # a record written before the fields existed: nothing is invented
    assert P.quiet_line_evidence(F.R_NO_CHANGE_TIME,
                                 {"confirmed_ms": 1.0, "evaluated_ms": 2.0}) \
        is None
    assert P.quiet_line_evidence(F.R_NO_CHANGE_TIME, None) is None
    assert P.quiet_line_evidence(F.R_NO_CHANGE_TIME,
                                 {"would_pass_on_confirmation": "yes"}) is None


# ═════════════════════════════════════════════════════════════════════
# 3 · THE LEDGER CODE: one evidence code, never a refusal
# ═════════════════════════════════════════════════════════════════════

def test_the_evidence_code_round_trips_and_is_declared_not_a_refusal():
    yes = ext.quiet_line_code({"would_pass_on_confirmation": True,
                               "no_observed_change_s": 7201.3,
                               "age_since_confirmation_s": 10.0,
                               "confirmation_limit_s": 30.0})
    assert yes == ("QUIET_LINE_WOULD_PASS_ON_CONFIRMATION:"
                   "no_observed_change_s=7201.3;age_since_confirmation_s=10;"
                   "limit_s=30")
    no = ext.quiet_line_code({"would_pass_on_confirmation": False,
                              "no_observed_change_s": 61.2,
                              "age_since_confirmation_s": None,
                              "confirmation_limit_s": 30.0})
    assert no == ("QUIET_LINE_WOULD_NOT_PASS_ON_CONFIRMATION:"
                  "no_observed_change_s=61.2;age_since_confirmation_s=none;"
                  "limit_s=30")
    assert ext.parse_quiet_line_code(yes) == {
        "would_pass_on_confirmation": True, "no_observed_change_s": 7201.3,
        "age_since_confirmation_s": 10.0, "limit_s": 30.0}
    assert ext.parse_quiet_line_code(no) == {
        "would_pass_on_confirmation": False, "no_observed_change_s": 61.2,
        "age_since_confirmation_s": None, "limit_s": 30.0}
    assert ext.quiet_line_code(None) is None and ext.quiet_line_code({}) is None
    assert ext.is_quiet_line_code(yes) and ext.is_quiet_line_code(no)
    assert not ext.is_quiet_line_code(F.R_NO_CHANGE_TIME)
    assert not ext.is_quiet_line_code(
        "%s:%s" % (ext.WS_REFERENCE_WRAPPER, F.R_NO_CHANGE_TIME))
    assert ext.parse_quiet_line_code(F.R_STALE) is None
    # the taxonomy: both heads declared not a refusal, with their reason
    for head in ext.QUIET_LINE_HEADS:
        assert head in TT.NOT_REFUSAL and head not in TT.TABLE
        k = RT.classify(yes if head == ext.QUIET_LINE_WOULD_PASS else no)
        assert k["classified"] is False and "NOT_A_REFUSAL" in k["why"]
        assert RT.normalize(yes if head == ext.QUIET_LINE_WOULD_PASS
                            else no) == head


def test_the_evidence_code_is_never_a_first_refusal_and_the_census_drops_it():
    code = ext.quiet_line_code({"would_pass_on_confirmation": True,
                                "no_observed_change_s": 10800.0,
                                "age_since_confirmation_s": 10.0,
                                "confirmation_limit_s": 30.0})
    ws = "%s:%s" % (ext.WS_REFERENCE_WRAPPER, F.R_NO_CHANGE_TIME)
    # the reactive path: wrapper, reason, evidence
    o = loop._event_outcome([ws, F.R_NO_CHANGE_TIME, code])
    assert o == {"outcome": "REFUSED", "first_refusal": ws,
                 "codes": [ws, F.R_NO_CHANGE_TIME, code]}
    # the stale-on-arrival path: the arrival code, the WS reason, evidence
    o = loop._event_outcome([loop.R_QUOTE_STALE_ON_ARRIVAL,
                             F.R_NO_CHANGE_TIME, code])
    assert o["first_refusal"] == loop.R_QUOTE_STALE_ON_ARRIVAL
    # an admitted row that carried evidence stays ADMITTED with NO first
    # refusal -- migration 137's check constraint would refuse the row
    # otherwise, and the writer would lose the whole cycle's rows
    o = loop._event_outcome([code, "ADMITTED"])
    assert o["outcome"] == "ADMITTED" and o["first_refusal"] is None
    o = loop._event_outcome([code, "DUPLICATE_OBSERVATION_SKIPPED"])
    assert o["outcome"] == "ALREADY_RECORDED" and o["first_refusal"] is None
    # the first-loss census reads the row's refusals without it
    row = {"first_refusal": loop.R_QUOTE_STALE_ON_ARRIVAL,
           "codes": json.dumps([loop.R_QUOTE_STALE_ON_ARRIVAL,
                                F.R_NO_CHANGE_TIME, code]),
           "outcome": "REFUSED", "stage": "2_FRESHNESS", "reach": 2}
    assert CFL._ledger_codes(row) == [loop.R_QUOTE_STALE_ON_ARRIVAL,
                                      F.R_NO_CHANGE_TIME]
    fl = CFL.first_loss_of_event(row, [], [], valuations_read=True,
                                 decisions_read=True)
    assert fl["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL
    assert fl["class"] == RT.SOFTWARE and code not in fl["codes"]
    # the one AGE_UNKNOWN first loss of the packet window, with its evidence
    row = {"first_refusal": F.R_NO_CHANGE_TIME,
           "codes": [F.R_NO_CHANGE_TIME, loop.R_PAYLOAD_HAS_NO_PINNACLE, code],
           "outcome": "REFUSED", "stage": "2_FRESHNESS", "reach": 2}
    fl = CFL.first_loss_of_event(row, [], [], valuations_read=True,
                                 decisions_read=True)
    assert fl["code"] == F.R_NO_CHANGE_TIME and code not in fl["codes"]
    assert fl["class"] == RT.SOFTWARE and fl["stage"] == "NORMALIZED"


def test_the_collector_builds_the_code_from_the_reads_own_provenance():
    c, ep = _cache()
    ev = T0 + HOURS
    _quiet_for_hours(c, ep, until=ev - 10_000)
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    evidence, code = loop.quiet_line_ledger_code(r["reason"], r["provenance"])
    assert evidence["would_pass_on_confirmation"] is True
    assert ext.parse_quiet_line_code(code) == {
        "would_pass_on_confirmation": True,
        "no_observed_change_s": r["provenance"]["no_observed_change_s"],
        "age_since_confirmation_s": 10.0, "limit_s": 30.0}
    # another reason, or no provenance: nothing recorded
    assert loop.quiet_line_ledger_code(P.R_NO_EXACT, r["provenance"]) == \
        (None, None)
    assert loop.quiet_line_ledger_code(F.R_NO_CHANGE_TIME, None) == (None, None)


def test_nothing_moved():
    """No freshness limit, confirmation kind, or read rule changed."""
    assert F.CENSUS_FRESH_S == 30.0 and loop.PINNACLE_MAX_AGE_S == 30
    assert F.PROVIDER_CONFIRMATIONS == (F.C_LIVE_REC, F.C_PREMATCH_MARKETS,
                                        F.C_MATCHUP_VERSION)
    assert F.FRESHNESS_BASIS == "LAST_OBSERVED_PRICE_CHANGE"
    assert not hasattr(F.FeedCache, "read_entry")
    assert "PINNAPI_ENTRY_ON_CONFIRMATION" not in os.environ
    import inspect
    assert "PINNAPI_ENTRY_ON_CONFIRMATION" not in inspect.getsource(F)
    assert "PINNAPI_ENTRY_ON_CONFIRMATION" not in inspect.getsource(P)


# ═════════════════════════════════════════════════════════════════════
# 4 · REAL POSTGRES: the row carries the fields; the readback reads them
# ═════════════════════════════════════════════════════════════════════

#: THE READBACK (research/rc63c_quiet_line_readback.sql reads production the
#: same way): the evidence code's head and its numbers off the jsonb codes
READBACK_SQL = """
    SELECT o.id, o.first_refusal, c AS code,
           split_part(c, ':', 1) = 'QUIET_LINE_WOULD_PASS_ON_CONFIRMATION'
               AS would_pass_on_confirmation,
           nullif(split_part(split_part(c, 'no_observed_change_s=', 2),
                             ';', 1), 'none')::float8 AS no_observed_change_s,
           nullif(split_part(split_part(c, 'age_since_confirmation_s=', 2),
                             ';', 1), 'none')::float8
               AS age_since_confirmation_s
      FROM ext_candidate_outcomes o, jsonb_array_elements_text(o.codes) c
     WHERE o.cycle_id = $1 AND c LIKE 'QUIET_LINE_%'
     ORDER BY o.id
"""


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


@pg
@pytest.mark.asyncio
async def test_a_ledger_row_carries_the_three_fields_through_the_collectors_writer():
    """The collector's own writer (`_persist_candidate_outcomes`) persists a
    refused row whose codes carry the evidence beside the freshness
    refusal; the readback SQL reads the three fields back; first_refusal
    is the refusal, and migration 137's constraint is satisfied for an
    ADMITTED row that carried evidence too."""
    c, ep = _cache()
    ev = T0 + HOURS
    _quiet_for_hours(c, ep, until=ev - 10_000)
    r = c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)
    _e, code = loop.quiet_line_ledger_code(r["reason"], r["provenance"])
    conn = await _connect()
    tag = "ql1-%s" % uuid.uuid4().hex[:10]
    try:
        rows = []
        for i, codes in enumerate((
                [loop.R_QUOTE_STALE_ON_ARRIVAL, F.R_NO_CHANGE_TIME, code],
                [F.R_NO_CHANGE_TIME, loop.R_PAYLOAD_HAS_NO_PINNACLE, code],
                [code, "ADMITTED"])):
            row = {"sport_key": "americanfootball_ncaaf", "family": "football",
                   "queue_position": i, "provider_event_id": "%s-%d" % (tag, i),
                   "home": "H", "away": "A", "commence_time": None,
                   "global_slug": None, "us_market_slug": None,
                   "stage": "2_FRESHNESS", "provider_lag_s": None,
                   "our_processing_s": None, "quote_age_s": None,
                   "mapped_by": None, "global_refusal_replaced": None}
            row.update(loop._event_outcome(codes))
            rows.append(row)
        out = await loop._persist_candidate_outcomes(
            conn, cycle_at=time.time(), rows=rows)
        assert out["ok"] is True and out["rows"] == 3, out
        cycle_id = out["cycle_id"]
        got = await conn.fetch(READBACK_SQL, cycle_id)
        assert len(got) == 3
        for g in got:
            assert g["would_pass_on_confirmation"] is True
            assert g["age_since_confirmation_s"] == 10.0
            assert g["no_observed_change_s"] == pytest.approx(
                r["provenance"]["no_observed_change_s"])
            assert ext.parse_quiet_line_code(g["code"])[
                "would_pass_on_confirmation"] is True
        firsts = [g["first_refusal"] for g in got]
        assert firsts == [loop.R_QUOTE_STALE_ON_ARRIVAL, F.R_NO_CHANGE_TIME,
                          None]
        outcomes = await conn.fetch(
            "SELECT outcome, first_refusal, codes FROM ext_candidate_outcomes"
            " WHERE cycle_id = $1 ORDER BY queue_position", cycle_id)
        assert [o["outcome"] for o in outcomes] == ["REFUSED", "REFUSED",
                                                    "ADMITTED"]
        # the census over the persisted rows: the refusal, never the evidence
        for o in outcomes[:2]:
            codes = json.loads(o["codes"]) if isinstance(o["codes"], str) \
                else list(o["codes"])
            assert code in codes
            fl = CFL.first_loss_of_event(
                {"first_refusal": o["first_refusal"], "codes": codes,
                 "outcome": "REFUSED", "stage": "2_FRESHNESS", "reach": 2},
                [], [], valuations_read=True, decisions_read=True)
            assert fl["code"] == o["first_refusal"] and code not in fl["codes"]
    finally:
        await conn.execute("DELETE FROM ext_candidate_outcomes "
                           "WHERE provider_event_id LIKE $1", tag + "%")
        await conn.close()


def _quiet_line_read():
    """A real cache read refusing R_NO_CHANGE_TIME for a price confirmed by
    provider-stamped lists 10 s before the read and unchanged for hours."""
    c, ep = _cache()
    ev = T0 + HOURS
    _quiet_for_hours(c, ep, until=ev - 10_000)
    return c.read(9, "s;0;m", evaluated_ms=ev, max_age_s=LIMIT)


async def _cycle_with(monkeypatch, *, ws_read, fallback: bool):
    """THE REAL SCHEDULED CYCLE over the production-shaped single-event
    fixture (tests/_emptybook_fixture), the provider's HTTP substituted, a
    metered quote 45 s old at receipt, and the PinnAPI read refusing as
    `ws_read` says (its provenance carried exactly as pinnapi_primary.select
    carries it: on the explain, and on the fallback when there is one)."""
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

    def _odds_event(stamp_epoch):
        stamp = _dt.datetime.fromtimestamp(
            stamp_epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ev = Fx.odds_event(time.time())
        for bk in ev["bookmakers"]:
            bk["last_update"] = stamp
            for m in bk["markets"]:
                if "last_update" in m:
                    m["last_update"] = stamp
        return ev

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": now,
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True, "events": [_odds_event(now - 45.0)],
                "received_at": now, "credits_used": "1",
                "credits_remaining": "9"}
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    real_primary = loop.primary_pinnacle_h2h

    def primary(event, *, received_at, family, at, explain=None):
        q = real_primary(event, received_at=received_at, family=family,
                         at=at, explain=explain)
        if isinstance(explain, dict):
            explain["reason"] = ws_read["reason"]
            explain["provenance"] = ws_read["provenance"]
        if q is None or not fallback:
            return None
        q = dict(q)
        q["reference_input"] = {"provider": P.LEGACY_PROVIDER,
                                "preferred_provider": P.PROVIDER,
                                "fallback_reason": ws_read["reason"],
                                "feed_read": ws_read["provenance"]}
        return q
    monkeypatch.setattr(loop, "primary_pinnacle_h2h", primary)
    handed: list = []

    async def paper_hook(conn_, valuation_id):
        handed.append(valuation_id)
    monkeypatch.setattr(loop, "_paper_valuation", paper_hook)
    out = await loop.cycle(conn)
    rows = await conn.fetch(
        "SELECT cycle_id, outcome, first_refusal, codes, provider_lag_s "
        "  FROM ext_candidate_outcomes "
        " WHERE provider_event_id LIKE 'odds-emptybook%' ORDER BY id")
    return conn, Fx, venue, out, rows, handed


def _codes(r):
    return json.loads(r["codes"]) if isinstance(r["codes"], str) \
        else list(r["codes"])


@pg
@pytest.mark.asyncio
async def test_a_whole_cycle_writes_the_evidence_beside_the_stale_on_arrival_refusal(
        monkeypatch):
    """THE 33 EVENTS' SHAPE. The metered fallback is 45 s old at receipt
    (QUOTE_STALE_ON_ARRIVAL, ours: the PinnAPI refusal beside it is
    FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE) and the WS price it fell back
    from was confirmed by the provider 10 s earlier: the row's codes carry
    the arrival code, the WS reason and the evidence code saying it would
    have passed on confirmation; first_refusal, the census class and the
    hand-off are unchanged; the cycle counts it; the readback reads it."""
    ws_read = _quiet_line_read()
    conn, Fx, venue, out, rows, handed = await _cycle_with(
        monkeypatch, ws_read=ws_read, fallback=True)
    try:
        assert out["ran"] is True, out.get("why")
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["quiet_line"] == {
            "would_pass_on_confirmation": 1,
            "would_not_pass_on_confirmation": 0}
        assert len(rows) == 1
        r = rows[0]
        assert r["outcome"] == "REFUSED"
        assert r["first_refusal"] == loop.R_QUOTE_STALE_ON_ARRIVAL
        codes = _codes(r)
        assert codes[:2] == [loop.R_QUOTE_STALE_ON_ARRIVAL, F.R_NO_CHANGE_TIME]
        assert codes[2] == ext.quiet_line_code(
            P.quiet_line_evidence(ws_read["reason"], ws_read["provenance"]))
        assert ext.parse_quiet_line_code(codes[2]) == {
            "would_pass_on_confirmation": True,
            "no_observed_change_s":
                ws_read["provenance"]["no_observed_change_s"],
            "age_since_confirmation_s": 10.0, "limit_s": 30.0}
        assert sum(ext.is_quiet_line_code(c) for c in codes) == 1
        assert r["provider_lag_s"] > LIMIT
        got = await conn.fetch(READBACK_SQL, r["cycle_id"])
        assert len(got) == 1 and got[0]["would_pass_on_confirmation"] is True
        assert got[0]["age_since_confirmation_s"] == 10.0
        # the heartbeat ledger entry carries the evidence beside the reason
        entry = next(e for e in out["mapped_candidate_ledger"]
                     if e.get("first_refusal") == loop.R_QUOTE_STALE_ON_ARRIVAL)
        assert entry["ws_refusal"] == F.R_NO_CHANGE_TIME
        assert entry["quiet_line"]["would_pass_on_confirmation"] is True
        assert entry["quiet_line"]["age_since_confirmation_s"] == 10.0
        # the census and the hand-off: unchanged
        fl = CFL.first_loss_of_event(
            {"first_refusal": r["first_refusal"], "codes": codes,
             "outcome": "REFUSED", "stage": "2_FRESHNESS", "reach": 2},
            [], [], valuations_read=True, decisions_read=True)
        assert fl["code"] == loop.R_QUOTE_STALE_ON_ARRIVAL
        assert fl["class"] == RT.SOFTWARE and codes[2] not in fl["codes"]
        assert handed == [] and venue.creates_sent() == []
    finally:
        await Fx.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_whole_cycle_writes_the_evidence_beside_a_first_loss_age_unknown(
        monkeypatch):
    """THE 34TH EVENT'S SHAPE (North Texas v Charlotte, 2026-10-10): no
    fallback at all, the WS read refused FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_
    CHANGE -- the first refusal IS the feed refusal, and the evidence code
    follows it on the row."""
    ws_read = _quiet_line_read()
    conn, Fx, venue, out, rows, handed = await _cycle_with(
        monkeypatch, ws_read=ws_read, fallback=False)
    try:
        assert out["ran"] is True, out.get("why")
        assert len(rows) == 1
        r = rows[0]
        codes = _codes(r)
        assert r["outcome"] == "REFUSED"
        assert r["first_refusal"] == F.R_NO_CHANGE_TIME
        assert codes[0] == F.R_NO_CHANGE_TIME
        quiet = [c for c in codes if ext.is_quiet_line_code(c)]
        assert len(quiet) == 1
        assert ext.parse_quiet_line_code(quiet[0])[
            "would_pass_on_confirmation"] is True
        assert out["latency"]["quiet_line"]["would_pass_on_confirmation"] == 1
        got = await conn.fetch(READBACK_SQL, r["cycle_id"])
        assert len(got) == 1 and got[0]["first_refusal"] == F.R_NO_CHANGE_TIME
        assert handed == [] and venue.creates_sent() == []
    finally:
        await Fx.clean(conn)
        await conn.close()
