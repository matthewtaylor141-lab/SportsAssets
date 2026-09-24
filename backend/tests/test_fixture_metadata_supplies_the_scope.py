"""THE SCOPE GUARD'S EVIDENCE, PARSED FROM THE SERVED PAYLOAD.

The fixture below is the shape the runner actually retrieved (job
107831250336, 2026-09-24T20:58:51Z), trimmed to the fields the parser reads
and with the Arizona fixture the acceptance position holds. Nothing here is
invented: if the endpoint's shape changes, these tests are what notices.
"""

from __future__ import annotations

from sportsassets import bettor_fixture_metadata as FM
from sportsassets import bettor_settlement_terms as ST

#: As served, trimmed. `status` carries the ACTUAL state, which is the field
#: a scheduled start could never supply.
_PAYLOAD = {"dates": [{"games": [
    {"gamePk": 824298, "gameType": "R", "scheduledInnings": 9,
     "doubleHeader": "N", "gameNumber": 1, "officialDate": "2026-09-24",
     "gameDate": "2026-09-24T19:10:00Z",
     "status": {"detailedState": "In Progress",
                "abstractGameState": "Live", "codedGameState": "I"},
     "teams": {"away": {"team": {"name": "Arizona Diamondbacks"}},
               "home": {"team": {"name": "Colorado Rockies"}}}},
    {"gamePk": 823411, "gameType": "R", "scheduledInnings": 9,
     "doubleHeader": "N", "gameNumber": 1, "officialDate": "2026-09-24",
     "gameDate": "2026-09-24T22:05:00Z",
     "status": {"detailedState": "Pre-Game",
                "abstractGameState": "Preview", "codedGameState": "P"},
     "teams": {"away": {"team": {"name": "Milwaukee Brewers"}},
               "home": {"team": {"name": "Philadelphia Phillies"}}}},
]}]}

_AT = "2026-09-24T20:58:51Z"
_URL = FM.SOURCE_URL % "2026-09-24"


def _ev(home="Colorado Rockies", away="Arizona Diamondbacks",
        payload=None):
    got = FM.parse_games(payload if payload is not None else _PAYLOAD)
    assert got["ok"], got
    m = FM.match_fixture(got["games"], home=home, away=away,
                         official_date="2026-09-24")
    return m, (FM.evidence_from(m["game"], retrieved_at=_AT, source_url=_URL,
                                condition_id="0xabc") if m["ok"] else None)


def test_the_phase_and_the_format_come_from_the_fixture_not_from_the_date():
    m, ev = _ev()
    assert m["ok"], m
    assert ev["ok"], ev["refusals"]
    assert ev["game_pk"] == 824298
    assert ev["game_type_raw"] == "R"
    assert ev["phase"] == ST.PHASE_REGULAR
    assert ev["scheduled_innings"] == 9
    assert ev["game_format"] == ST.FMT_NINE
    # AND THE SCOPE GUARD ADMITS THE CAPTURED TERMS ON THAT EVIDENCE
    sc = ST.admit_scope(sport_family="baseball", market="h2h",
                        phase=ev["phase"], game_format=ev["game_format"])
    assert sc["ok"] is True, sc
    assert ST.book_terms(sport_family="baseball", market="h2h",
                         context=ST.CTX_PRE_GAME, phase=ev["phase"],
                         game_format=ev["game_format"])


def test_the_evidence_carries_its_source_binding_and_retrieval_time():
    _, ev = _ev()
    assert ev["source"] == FM.SOURCE
    assert ev["source_url"].startswith("https://statsapi.mlb.com/")
    assert ev["retrieved_at"] == _AT
    # the FIXTURE BINDING, so a row can be checked against what it describes
    assert ev["home"] == "Colorado Rockies"
    assert ev["away"] == "Arizona Diamondbacks"
    assert ev["official_date"] == "2026-09-24"
    assert ev["condition_id"] == "0xabc"


def test_the_actual_state_supplies_the_context_a_schedule_could_not():
    _, ev = _ev()
    assert ev["play_has_begun"] is True
    assert ev["event_state_raw"] == "In Progress"
    assert ev["start_evidence"] == ST.SE_ACTUAL_REPORTED
    assert ev["actual_start_at"] is not None
    # a quote after the reported start is IN_PLAY, on ACTUAL evidence
    after = FM.context_for(ev, observed_at=ev["actual_start_at"] + 600.0)
    assert after["context"] == ST.CTX_LIVE, after
    # and one before it is PRE_GAME, also on actual evidence
    before = FM.context_for(ev, observed_at=ev["actual_start_at"] - 600.0)
    assert before["context"] == ST.CTX_PRE_GAME, before


def test_a_not_started_report_covers_only_quotes_taken_before_it():
    _, ev = _ev(home="Philadelphia Phillies", away="Milwaukee Brewers")
    assert ev["play_has_begun"] is False
    at = FM._epoch(_AT)
    ok = FM.context_for(ev, observed_at=at - 60.0)
    assert ok["context"] == ST.CTX_PRE_GAME
    assert "NOT STARTED" in ok["why"]
    # AFTER the report, the state is simply not covered -- not "still
    # pre-game". The report is a fact about one instant.
    later = FM.context_for(ev, observed_at=at + 3600.0)
    assert later["context"] is None
    assert later["refusal"] == ST.R_CONTEXT_UNKNOWN


def test_a_doubleheader_is_ambiguous_and_refused_rather_than_picked():
    """The format is what is being established, so choosing one of two games
    would answer the question by assuming it."""
    dh = {"dates": [{"games": [
        dict(_PAYLOAD["dates"][0]["games"][0], gamePk=1, gameNumber=1,
             doubleHeader="Y", scheduledInnings=7),
        dict(_PAYLOAD["dates"][0]["games"][0], gamePk=2, gameNumber=2,
             doubleHeader="Y", scheduledInnings=7),
    ]}]}
    m, _ = _ev(payload=dh)
    assert m["ok"] is False
    assert m["refusal"] == FM.R_AMBIGUOUS
    assert m["matched"] == [1, 2]
    assert "DOUBLEHEADER" in m["why"]


def test_a_postseason_fixture_is_a_known_uncovered_phase_not_an_unknown_one():
    ps = {"dates": [{"games": [
        dict(_PAYLOAD["dates"][0]["games"][0], gameType="D")]}]}
    _, ev = _ev(payload=ps)
    assert ev["phase"] == ST.PHASE_PLAYOFF
    assert ev["ok"] is True, "the phase IS established -- it is just excluded"
    sc = ST.admit_scope(sport_family="baseball", market="h2h",
                        phase=ev["phase"], game_format=ev["game_format"])
    assert sc["ok"] is False
    assert ST.R_PHASE_EXCLUDED in sc["refusals"]


def test_an_undeclared_game_type_or_state_is_refused_by_name():
    odd = {"dates": [{"games": [
        dict(_PAYLOAD["dates"][0]["games"][0], gameType="Z")]}]}
    _, ev = _ev(payload=odd)
    assert FM.R_GAME_TYPE_UNDECLARED in ev["refusals"]
    assert ev["phase"] is None and ev["phase_uncovered"] is None

    weird = {"dates": [{"games": [
        dict(_PAYLOAD["dates"][0]["games"][0],
             status={"detailedState": "Rain Check Pending"})]}]}
    _, ev2 = _ev(payload=weird)
    assert FM.R_STATE_UNDECLARED in ev2["refusals"]
    assert ev2["play_has_begun"] is None
    assert FM.context_for(ev2, observed_at=0.0)["context"] is None

    seven = {"dates": [{"games": [
        dict(_PAYLOAD["dates"][0]["games"][0], scheduledInnings=7)]}]}
    _, ev3 = _ev(payload=seven)
    assert ev3["game_format"] == ST.FMT_SEVEN
    assert ST.admit_scope(sport_family="baseball", market="h2h",
                          phase=ev3["phase"],
                          game_format=ev3["game_format"])["ok"] is False


def test_a_terminal_state_is_carried_through_as_a_hint_not_a_verdict():
    for state, cond in (("Suspended", ST.C_SUSPENDED_BEYOND),
                        ("Postponed", ST.C_NOT_PLAYED),
                        ("Completed Early", ST.C_CALLED_FINAL)):
        p = {"dates": [{"games": [
            dict(_PAYLOAD["dates"][0]["games"][0],
                 status={"detailedState": state})]}]}
        _, ev = _ev(payload=p)
        assert ev["terminal_hint"] == cond, (state, ev["terminal_hint"])


def test_an_unreadable_payload_is_named_rather_than_empty():
    assert FM.parse_games(None)["games"] == []
    assert FM.parse_games({"dates": "not a list"})["refusal"] == FM.R_PAYLOAD
    m = FM.match_fixture([], home="a", away="b", official_date="2026-09-24")
    assert m["refusal"] == FM.R_NO_MATCH


def test_the_probe_that_established_the_shape_is_recorded():
    d = FM.describe()
    assert d["shape_probe"]["http"] == 200
    assert d["shape_probe"]["job"].startswith("https://github.com/")
    assert "gameType" in d["shape_probe"]["fields_used"]
    assert "status.detailedState" in d["shape_probe"]["fields_used"]
    assert d["an_undeclared_value_is_refused"]
