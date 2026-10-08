"""TWO SOFTWARE FIRST LOSSES OF OUR OWN MAKING, CLOSED AT THEIR CAUSE.

PRODUCTION (read-only readback 2026-10-08 01:40Z, release 08828d04, the
coverage first-loss census and the capital-authority blocker census):

  PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE  4 events in 1 h, 23 in 24 h
      every one decided by PINNACLE_COMPLETED_GAME_PAPER /
      PINNACLE_EXPLORATION_PAPER (blocker census: 1,236 rows, those two
      strategies only). The census reads the lane code carried right behind
      the wrapper (coverage_first_loss.LANE_WRAPPERS, d075e12f), and
      `contract_match` carries it -- but `completed_game_match`, the match
      every completed-game-kind policy records, rebuilt its refusals from
      the checks and DROPPED the carried codes, so the census could only
      class those decisions by the wrapper.

  PINNAPI_PRIMARY_CLOCK_INVALID  2 events in 24 h, 319 decision rows over
      151 contract sides
      the paper recheck (`pinnapi_primary.validate`) compared the cache
      quote's `received_ms` -- the arrival of the LATEST frame carrying the
      market, re-stamped by every live push of the event and every prematch
      re-assertion, price unchanged -- with a decision instant read BEFORE
      the policy's awaited reads. An unchanged price was refused as a clock
      fault.

Neither fix moves a threshold, the 30 s rule, a verdict or a class: the
wrapper still refuses and the census classes it by the carried code's OWN
class; a changed price is refused by its real name and a price we did not yet
hold at the evaluation instant is still CLOCK_INVALID.
"""
from __future__ import annotations

import copy
import inspect
from datetime import datetime, timezone

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_market_family as MF
from sportsassets import coverage_first_loss as CFL
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK

WRAP = PB.R_PROBABILITY_UNQUALIFIED
SLUG = "aec-mlb-cle-cws-2026-10-07"
PICK = "Cleveland Guardians"


def _cand(refusals, *, market="h2h"):
    return {"refusals": list(refusals), "us_market_slug": SLUG,
            "payout_event": PICK, "period": "FULL_GAME", "fixture": {"id": 1},
            "side": PD.DP.LONG, "market": market, "line": None,
            "sport_family": "baseball",
            "settlement": {"compatibility": "COMPATIBLE",
                           "overall_established": True}}


#: the venue's MLB winner wording shape (VENUE_GRADING_TEMPLATES baseball),
#: so the ordinary grading period is established and only the probability
#: check refuses
VENUE_TEXT = ("This market will settle to the winner of the Cleveland "
              "Guardians vs Chicago White Sox game scheduled for October 7, "
              "2026. Extra innings are included if played.")


def _row():
    return {"contract_selection": PICK, "payout_event": PICK,
            "probability_event": PICK, "sport_family": "baseball",
            "settlement_comparison": {"venue_rules_text": VENUE_TEXT}}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE COMPLETED-GAME MATCH CARRIES THE LANE'S CODES BEHIND THE WRAPPER
# ═════════════════════════════════════════════════════════════════════

#: the shape of the production rows behind the wrapper (research-sql run
#: 37411912022 P1: a stale metered quote leaves no fair value -- QUOTE_STALE,
#: then the gate's NO_QUALIFIED_MODEL / INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED)
STALE_ROW = ["QUOTE_STALE", "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
             "NO_QUALIFIED_MODEL", "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED",
             "EXECUTION_ESTIMATE_NOT_IDENTIFIED"]


def test_the_completed_game_match_carries_the_lane_codes_behind_the_wrapper():
    m = PB.completed_game_match(_cand(STALE_ROW), _row())
    refs = m["refusals"]
    i = refs.index(WRAP)
    assert refs[i + 1:i + 3] == ["NO_QUALIFIED_MODEL",
                                 "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED"]
    # exactly the probability-stage codes, as the strict match carries them
    base = PB.contract_match(_cand(STALE_ROW), _row())["refusals"]
    j = base.index(WRAP)
    assert refs[i + 1:i + 3] == base[j + 1:j + 3]
    # freshness, venue and execution codes are not carried
    for c in ("QUOTE_STALE", "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
              "EXECUTION_ESTIMATE_NOT_IDENTIFIED"):
        assert c not in refs
    # the verdict is unchanged: the wrapper still refuses, by its check
    assert m["established"] is False
    chk = next(c for c in m["checks"]
               if c["check"] == "probability_qualified_by_the_lane")
    assert chk["passed"] is False and chk["refusal"] == WRAP
    assert chk["lane_refusals"] == ["NO_QUALIFIED_MODEL",
                                    "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED"]


def test_no_lane_code_rides_when_the_wrapper_does_not_fire():
    m = PB.completed_game_match(_cand(["QUOTE_STALE",
                                       "EXECUTION_ESTIMATE_NOT_IDENTIFIED"]),
                                _row())
    assert WRAP not in m["refusals"]
    assert not any(ext.STAGE_OF.get(c) == "1_PROBABILITY"
                   for c in m["refusals"])


def test_a_code_the_policy_does_not_apply_is_not_carried():
    # PinnAPI as the sole authority: the multi-book floor is NOT applied
    # (recorded under not_applied_by_policy), so it neither fires the
    # wrapper nor rides behind it
    cand = _cand([ext.R_THIN_OUTCOME])
    cand["pinnacle"] = {"provider": PB.PINNAPI_PROVIDER}
    row = dict(_row(), provider=PB.PINNAPI_PROVIDER, outcome_books=1)
    m = PB.completed_game_match(cand, row)
    assert WRAP not in m["refusals"]
    assert ext.R_THIN_OUTCOME not in m["refusals"]


def test_the_line_market_match_carries_them_too():
    m = PB.completed_game_match(
        _cand(["NO_QUALIFIED_MODEL"], market=MF.LINE_FAMILIES[0]), _row())
    refs = m["refusals"]
    assert refs[refs.index(WRAP) + 1] == "NO_QUALIFIED_MODEL"


def test_every_completed_game_kind_policy_records_the_match_refusals():
    # the completed-game, maker and exploration decisions extend their
    # refusals with the match's own list -- the list carrying the codes
    for fn in (PB.decide_one, PMK.decide_one, PEX.decide_one):
        src = inspect.getsource(fn)
        assert "completed_game_match(" in src
        assert 'refusals.extend(match["refusals"])' in src


def test_the_census_classes_a_completed_game_decision_by_the_carried_code():
    def first_loss(lane):
        match = PB.completed_game_match(_cand(lane), _row())
        # as decide_one records it: the match's refusals, then the
        # re-aged reading's own refusal
        dec = {"verdict": "REFUSE",
               "refusals": match["refusals"] + [PD.DP.R_NO_PINNACLE]}
        return CFL.first_loss_of_event(
            {}, [{"refusals": lane}], [dec], valuations_read=True,
            decisions_read=True)
    # an EXTERNAL lane code is classed EXTERNAL, by its own table entry
    fl = first_loss(["THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK"])
    assert fl["code"] == "THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK"
    assert fl["class"] == CFL.EXTERNAL and fl["carried_by"] == WRAP
    # a SOFTWARE lane code stays SOFTWARE, by its own name -- never hidden
    fl = first_loss(STALE_ROW)
    assert fl["code"] == "NO_QUALIFIED_MODEL"
    assert fl["class"] == RT.SOFTWARE and fl["carried_by"] == WRAP
    assert fl["stage"] == "FAIR_VALUE"


# ═════════════════════════════════════════════════════════════════════
# 2 · THE RECHECK'S CLOCK GUARD: THE PRICE, NOT ITS LATEST RE-ASSERTION
# ═════════════════════════════════════════════════════════════════════

AT = 1791050000.0
KEY = F.FULL_GAME_MONEYLINE_KEY
SID = 6
TEAMS = ("Atlanta Braves", "Los Angeles Dodgers")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _market(home=-120):
    return {"key": KEY, "type": "moneyline", "period": 0, "status": "open",
            "prices": [{"designation": "home", "price": home},
                       {"designation": "away", "price": 110}]}


def _seed(live=False):
    """A cache whose money line CHANGED at AT-2 (received AT-2+5 ms), and
    the WS quote `select` builds from it at AT."""
    stream = "live" if live else "prematch"
    start = AT - 1800 if live else AT + 3600
    event = {"id": 123, "startTime": _iso(start), "isLive": live,
             "participants": [{"name": TEAMS[0], "alignment": "home"},
                              {"name": TEAMS[1], "alignment": "away"}],
             "markets": [_market(-125)]}
    c = F.FeedCache()
    e = c.new_connection([(stream, SID)])
    c.apply({"type": "snapshot", "stream": stream, "sport_id": SID,
             "ts": (AT - 60) * 1000, "events": [event]}, epoch=e,
            received_ms=(AT - 60) * 1000 + 5)
    c.apply(_frame(live, _market(-120), AT - 2), epoch=e,
            received_ms=(AT - 2) * 1000 + 5)
    disc = {"id": "discovery-123", "home_team": TEAMS[0],
            "away_team": TEAMS[1], "commence_time": _iso(start),
            "bookmakers": []}
    q = P.select(c, disc, None, family="baseball", sharp_books={"pinnacle"},
                 at=AT, max_age_s=30.0, runtime_id="r1")
    assert q is not None and q["reference_input"]["provider"] == P.PROVIDER
    return c, e, q


def _frame(live, market, ts):
    if live:
        return {"type": "live", "op": "upd", "sport_id": SID,
                "ts": ts * 1000, "rec": {"id": 123, "markets": [market]}}
    return {"type": "prematch_markets", "matchup_id": 123, "sport_id": SID,
            "ts": ts * 1000, "data": [market]}


def _check(c, q, at):
    return P.validate(c, q, at=at, max_age_s=30.0, runtime_id="r1")


def test_a_re_assertion_after_the_decision_instant_is_not_a_clock_fault():
    for live in (False, True):
        c, e, q = _seed(live)
        # the decision instant is read; then, during the policy's awaited
        # reads, a frame re-asserts the SAME price (a live push of the
        # event, a prematch list) and re-stamps received_ms
        at = AT + 0.5
        c.apply(_frame(live, _market(-120), AT + 1), epoch=e,
                received_ms=(AT + 1) * 1000 + 5)
        held = c.quotes[(123, KEY)]
        assert held.received_ms > at * 1000           # the old trigger
        assert held.change_ms == (AT - 2) * 1000      # substance unchanged
        assert held.first_observed_ms == (AT - 2) * 1000 + 5
        chk = _check(c, q, at)
        assert chk["ok"], (live, chk)
        # aged from the CHANGE at the decision instant, unchanged
        assert chk["provenance"]["quote_age_s"] == 2.5


def test_a_price_that_changed_is_input_changed_by_its_real_name():
    c, e, q = _seed()
    # stamped before the decision instant, arriving after it (network lag):
    # the read is fresh at `at`, the price is not the one valued
    c.apply(_frame(False, _market(-140), AT + 0.2), epoch=e,
            received_ms=(AT + 1) * 1000 + 5)
    assert _check(c, q, AT + 0.5)["reason"] == "PINNAPI_PRIMARY_INPUT_CHANGED"


def test_an_instant_before_we_held_the_price_is_still_a_clock_fault():
    c, e, q = _seed()
    # after the provider's change stamp (AT-2) but before our receipt of it
    # (AT-2 + 5 ms): we did not hold this price at that instant
    assert _check(c, q, AT - 2 + 0.001)["reason"] == \
        "PINNAPI_PRIMARY_CLOCK_INVALID"


def test_a_quote_without_a_first_observation_falls_back_to_its_receipt():
    c, e, q = _seed()
    c.apply(_frame(False, _market(-120), AT + 1), epoch=e,
            received_ms=(AT + 1) * 1000 + 5)
    c.quotes[(123, KEY)].first_observed_ms = None
    assert _check(c, q, AT + 0.5)["reason"] == "PINNAPI_PRIMARY_CLOCK_INVALID"
    assert _check(c, q, AT + 2)["ok"]


def test_the_30_s_rule_is_unchanged():
    c, e, q = _seed()
    c.apply(_frame(False, _market(-120), AT + 1), epoch=e,
            received_ms=(AT + 1) * 1000 + 5)
    # change at AT-2: age 30.0 s at AT+28 is inside, a hair past is not
    assert _check(c, q, AT + 28)["ok"]
    late = _check(c, q, AT + 28.0001)
    assert late["ok"] is False and late["reason"] == F.R_STALE
    # a change stamped after the decision instant is still in the future
    c.apply(_frame(False, _market(-150), AT + 3), epoch=e,
            received_ms=(AT + 3) * 1000 + 5)
    assert _check(c, q, AT + 2.5)["reason"] == F.R_FUTURE


def test_select_keeps_its_own_guard_at_its_own_instant():
    # `select` runs at time.time(): a quote received after its instant is
    # still refused there (unchanged)
    c, e, q = _seed()
    disc = {"id": "d", "home_team": TEAMS[0], "away_team": TEAMS[1],
            "commence_time": _iso(AT + 3600), "bookmakers": []}
    why: dict = {}
    assert P.select(c, disc, None, family="baseball",
                    sharp_books={"pinnacle"}, at=AT - 2 + 0.001, max_age_s=30.0,
                    runtime_id="r1", explain=why) is None
    assert why["reason"] == "PINNAPI_PRIMARY_CLOCK_INVALID"


def test_supersession_now_sees_a_changed_price_it_was_blind_to():
    # a changed price used to read CLOCK_INVALID when its frame arrived
    # after the decision instant, and supersession only acts on
    # INPUT_CHANGED; now it is offered the case (and still decides by its
    # own conditions: the newer price must be fresh at the instant)
    c, e, q = _seed()
    c.apply(_frame(False, _market(-140), AT + 0.2), epoch=e,
            received_ms=(AT + 1) * 1000 + 5)
    at = AT + 0.5
    assert _check(c, q, at)["reason"] == "PINNAPI_PRIMARY_INPUT_CHANGED"
    sup = P.supersession(c, q, at=at, max_age_s=30.0, runtime_id="r1")
    assert sup is not None and sup["reason"] == P.R_SUPERSEDED
    assert sup["newer_change_ms"] == (AT + 0.2) * 1000
