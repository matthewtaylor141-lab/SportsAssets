"""M5 review pins (2026-09-07): the mutants the builder's tests did not
kill, and the findings of hard2/M5_review.md. Fixtures are the
builder's (tests/test_c6_cfb_team.py: the probe's verbatim team dicts
for lou-miss, the wisc-nd dicts built in the probe's shape).

Two kinds of test: the mutant killers, which passed on the builder's
first patch; and the FINDING pins, which were xfail(strict=True) until
M5 v2 fixed each finding -- the markers are gone, every pin passes.
Named test_c6_cfb_review_pins.py: test_c6_review_pins.py on the branch
tip is M2's exact-score/halftime pins. M5 v2 arms the identity switch on
the moneyline pins: HIGH-1 made that arm dark without it.
"""
from __future__ import annotations

import asyncio

import pytest

from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import premap
from tests.test_c1_round3 import _PremapPool, _row
from tests.test_c4_cfb_spreads import _state
from tests.test_c6_cfb_team import (AEC_LM, AEC_WN, ATC_WN_1H_ND, EV_LM, LM, MK_WN, Q_LM, Q_WN, TEAM_MISS,
                                    TEAM_ND, TEAM_WISC, WN, TestTheMoneyline, _aec, _board, _con, _explain,
                                    _resolve, _venue_lm)

CON_ND = _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?")[ATC_WN_1H_ND]


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# ------------------------------------------------ the mutants that survived

class TestTheTruthNeedsTheAbbreviationAndTheOtherSide:
    def test_mu4_the_school_alone_never_certifies(self, armed):
        # side 1 states safeName 'Notre Dame' but abbreviation 'wisc' (the
        # other code) / '' (none): the school agreeing with the winner row
        # is not the binding -- abbreviation == code is half of it
        wrong = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="wisc"))
        assert map_lane.grammar_truth(CON_ND, wrong, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        none = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation=""))
        assert map_lane.grammar_truth(CON_ND, none, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        strange = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="notre dame"))
        assert map_lane.grammar_truth(CON_ND, strange, 1, code="nd", school="Notre Dame")[0] == "mismatch"

    def test_mu3_the_other_side_claiming_the_code_or_the_school_is_a_mismatch(self, armed):
        # side 0 (Badgers) ALSO states abbreviation 'nd' / school 'Notre Dame':
        # the venue names one school on both sides -- never ok
        dup = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_ND, TEAM_ND)
        assert map_lane.grammar_truth(CON_ND, dup, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        by_school = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", dict(TEAM_WISC, safeName="Notre Dame"), TEAM_ND)
        assert map_lane.grammar_truth(CON_ND, by_school, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        by_code = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", dict(TEAM_WISC, abbreviation="nd"), TEAM_ND)
        assert map_lane.grammar_truth(CON_ND, by_code, 1, code="nd", school="Notre Dame")[0] == "mismatch"

    def test_mu3_end_to_end_the_class_trips(self, monkeypatch):
        dup = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", dict(TEAM_WISC, safeName="Notre Dame"), TEAM_ND)
        why, st, _ = TestTheMoneyline()._admission(monkeypatch, dup, _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?"))
        assert why == "side_echo_mismatch" and st["tripped"] is True and not st.get("certified")


class TestTheSidePickNeedsTheAbbreviation:
    def test_mu5_a_school_without_the_code_puts_him_nowhere(self, armed):
        # side 1 safeName 'Notre Dame' with an abbreviation that is neither
        # code ('xx'), or none: the school alone is not the venue's binding
        for team in (dict(TEAM_ND, abbreviation="xx"), dict(TEAM_ND, abbreviation=""),
                     {"safeName": "Notre Dame", "name": "Fighting Irish"}):
            mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, team)
            hit, why = map_lane.aec_code_side(WN, "Notre Dame", mk, 1)
            assert hit is None and why == "side_code_unmatched", team


class TestTheEventTitleMustNameHisOutcomeOnHisPosition:
    def test_mu6_a_second_name_of_his_on_his_position_is_a_collision(self, armed):
        # no winner rows (the code rules read the names): 'Ole Miss' /
        # 'Spread: Ole Miss (-1.5)' with event title 'Louisville vs.
        # Mississippi State' -- 'Mississippi State' reads miss by the prefix
        # and sits on his position, but it is not his outcome: his own feed
        # contradicts itself, nothing maps
        rows = _board(_venue_lm(winner=False))
        ev = "Louisville vs. Mississippi State"
        assert _resolve(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", ev) is None
        ex = _explain(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", ev)
        assert ex["split"] == "spread:event-collision" and ex["c3"]["event_hits"] == [["lou"], ["miss"]]


class TestTheDrawRowIsNeverAContract:
    def test_mu11_a_draw_slug_with_a_school_shaped_question(self, armed):
        # the venue mislabelling: a `-draw` slug whose question names a
        # school is never the code's contract (the slug decides, not the
        # question alone)
        rows = [_row(f"atc-{WN}-winner-1h-draw", "Will Notre Dame win the first half?"),
                _row(f"atc-{WN}-winner-1h-wisc", "Will Wisconsin win the first half?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == []
        draw = _con(f"atc-{WN}-winner-1h-draw", "Will Notre Dame win the first half?")[f"atc-{WN}-winner-1h-draw"]
        assert map_lane.grammar_truth(draw, MK_WN, 1, code="nd", school="Notre Dame")[0] != "ok"


# --------------------------------------------------- the findings (xfail)

def test_finding_h1_off_the_switch_the_moneyline_arm_does_not_run(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
    hit, why = map_lane.aec_code_side(WN, "Notre Dame", MK_WN, 1)
    assert hit is None and why == "side_code_unmatched"
    rows = [_row(ATC_WN_1H_ND, "Will Notre Dame win the first half?")]
    assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == []


def test_finding_m1_a_half_binding_does_not_certify_the_moneyline(armed):
    half = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, TEAM_ND)
    assert map_lane.aec_code_side(WN, "Notre Dame", half, 1)[1] == "side_code_unmatched"
    assert map_lane.grammar_truth(CON_ND, half, 1, code="nd", school="Notre Dame")[0] == "unverified"


def test_finding_m2_a_plain_contract_never_overrides_the_sides_own_code(armed):
    # C1's plain contract names 'Fighting Irish' at side 1, but the side's
    # own field says abbreviation 'wisc' -- the venue contradicting itself
    plain = {"slug": f"atc-{WN}-nd", "outcome": "Fighting Irish", "title": "Fighting Irish"}
    wrong = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="wisc"))
    assert map_lane.grammar_truth(plain, wrong, 1, code="nd", school="Notre Dame")[0] == "mismatch"


def test_finding_m3_a_class_record_contradicting_the_team_field_refuses(armed):
    rows = _board(_venue_lm())
    rec = {"outcome_desc": "Cardinals", "side_index": 1, "his_slug": LM, "at": 1.0}
    assert _resolve(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM,
                    _state({AEC_LM: rec})) is None


def test_finding_l1_a_winner_row_of_another_event_or_date_never_certifies(armed):
    other = _con("atc-cfb-wisc-nd-2026-09-13-winner-1h-nd", "Will Notre Dame win the first half?")
    con = other["atc-cfb-wisc-nd-2026-09-13-winner-1h-nd"]
    assert map_lane.grammar_truth(con, MK_WN, 1, code="nd", school="Notre Dame")[0] != "ok"


# ------------------------------------ the v2 re-check (hard2/M5_review_v2.md)
# Killers for the mutants the v2 tests did not kill by behaviour (MU18,
# MU23c, MU33, MU37, MU39, MU40, MU41 of hard2/m5_mutants_v2.py) and the
# v2 findings (strict xfail until fixed, the v1 convention).

class TestTheV2Survivors:
    def test_mu18_the_side_stating_no_team_is_unverified_never_a_trip(self, armed):
        # side 1 (his) states no team while side 0 does: the witness is
        # absent, not contradicted -- unverified, nothing tripped
        mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, None)
        v, d = map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")
        assert v == "unverified" and "states no team" in d
        mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, {"ordering": "home", "id": 2})
        assert map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")[0] == "unverified"

    def test_mu23c_the_truth_arm_is_college_football_only(self, armed):
        # the same shape under another league code reads nothing (LOW-2), at
        # the third site too: the winner-row truth is never read for epl
        soc = dict(_aec("aec-epl-eve-mnu-2026-09-06", "Everton vs Man United", "Toffees", "Red Devils",
                        {"abbreviation": "eve", "safeName": "Everton"},
                        {"abbreviation": "mnu", "safeName": "Manchester United"}),
                   title="Everton vs. Manchester United")
        con = _con("atc-epl-eve-mnu-2026-09-06-winner-1h-mnu", "Will Manchester United win the first half?")
        v, _d = map_lane.grammar_truth(con["atc-epl-eve-mnu-2026-09-06-winner-1h-mnu"], soc, 1,
                                       code="mnu", school="Manchester United")
        assert v == "unverified"
        # and a cfb slug on his side never reaches it through an epl market
        v, _d = map_lane.grammar_truth(CON_ND, dict(MK_WN, slug="aec-epl-wisc-nd-2026-09-06"), 1,
                                       code="nd", school="Notre Dame")
        assert v == "unverified"

    def test_mu33_names_meet_by_token_set_equality_never_containment(self, armed):
        for a, b in (("texas", "texas state"), ("washington", "washington state"), ("michigan", "michigan state"),
                     ("notre dame", "notre dame fighting irish"), ("miss", "ole miss"), ("smu", "smu mustangs")):
            assert map_lane.same_name(a, b) is False and map_lane.same_name(b, a) is False, (a, b)
        # end to end: the side's school containing the winner row's school is a mismatch, not ok
        mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, safeName="Notre Dame Fighting Irish"))
        assert map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        assert map_lane.aec_code_side(WN, "Notre Dame", mk, 1)[1] == "side_code_unmatched"
        # the witness: 'Wisconsin State' under -wisc beside 'Wisconsin' is two schools on one code
        rows = _board([(AEC_WN, m) for _ev, m in __import__("tests.test_c6_cfb_team", fromlist=["_winner_rows"])
                       ._winner_rows(AEC_WN, ("wisc", "nd"), ("Wisconsin", "Notre Dame"))])
        rows.append({"identifier": f"atc-{WN}-winner-4q-wisc", "question": "Will Wisconsin State win the fourth quarter?"})
        assert map_lane.code_school(rows, "cfb", "wisc", "nd", "2026-09-06") == (None, "spread:code-witness-disagree")

    def test_mu37_the_winner_row_grammar_is_exactly_the_segment_question(self, armed):
        for q in ("Will Notre Dame win?", "Will Notre Dame win the game?", "Will Notre Dame win by 7 or more points?",
                  "Will Notre Dame win the first half by 3?", "Will Notre Dame win the first half or the second half?",
                  "Notre Dame to win the first half?", "Will Notre Dame win the first period?"):
            assert map_lane.winner_school(q) is None, q
        con = _con(ATC_WN_1H_ND, "Will Notre Dame win the game?")[ATC_WN_1H_ND]
        assert map_lane.grammar_truth(con, MK_WN, 1, code="nd", school="Notre Dame")[0] == "unverified"
        rows = [_row(ATC_WN_1H_ND, "Will Notre Dame win by 7 or more points?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == []

    def test_mu39_a_tail_bearing_aec_slug_is_no_head(self, armed):
        assert map_lane.aec_head({"slug": "aec-cfb-wisc-nd-2026-09-06"}) == ("cfb", "wisc", "nd", "2026-09-06")
        assert map_lane.aec_head({"slug": "aec-cfb-wisc-nd-2026-09-06-nd"}) is None
        assert map_lane.aec_head({"slug": "atc-cfb-wisc-nd-2026-09-06"}) is None
        assert map_lane.aec_head(None) is None and map_lane.aec_head({}) is None
        mk = dict(MK_WN, slug="aec-cfb-wisc-nd-2026-09-06-nd")
        assert map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")[0] == "unverified"
        # the head is the MARKET's, not the contract's: an aec market of another
        # date never reads the 09-06 winner row as its own
        assert map_lane.grammar_truth(CON_ND, dict(MK_WN, slug="aec-cfb-wisc-nd-2026-09-13"), 1,
                                      code="nd", school="Notre Dame")[0] == "unverified"

    def test_mu40_only_a_winner_row_is_ever_the_codes_contract(self, armed):
        # a dashed row that is not `winner-<seg>-<code>` never becomes a
        # candidate, whatever its question says
        for ident in (f"atc-{WN}-prop-1h-nd", f"atc-{WN}-1h-nd", f"atc-{WN}-nd-1h-winner", f"atc-{WN}-winner-nd"):
            rows = [_row(ident, "Will Notre Dame win the first half?")]
            assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == [], ident
        # beside the winner row, such a row changes nothing either
        rows = [_row(ATC_WN_1H_ND, "Will Notre Dame win the first half?"),
                _row(f"atc-{WN}-prop-1h-nd", "Will Notre Dame win the first half?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == [ATC_WN_1H_ND]

    def test_mu41_the_sweep_reprobes_after_it_adds_the_columns(self, monkeypatch):
        # behaviour, not source: an 'absent' verdict cached before the sweep's
        # ALTERs is forgotten once they ran, and the next reader sees them
        class _P:
            def __init__(self):
                self.n = 0

            async def execute(self, sql, *a):
                if "ADD COLUMN IF NOT EXISTS team_abbr" in sql:
                    self.n = 7

            async def fetchval(self, sql, *a):
                assert "information_schema" in sql
                return self.n

        import time as _time
        monkeypatch.setattr(premap, "_TEAM_COLS_STATE", {"present": False, "at": _time.time()})
        p = _P()
        assert asyncio.run(premap.team_select_cols(p)) == ""
        asyncio.run(premap._ensure_table(p))
        assert premap._TEAM_COLS_STATE["present"] is None
        assert asyncio.run(premap.team_select_cols(p)) == premap.TEAM_SELECT_COLS


class TestTheOtherSideMustStateBothHalves:
    """v2 LOW-1: the moneyline readers take an other side that states an
    abbreviation alone, or a school alone, as 'a team claiming neither'
    and certify; the spread reader on the same row (_c6_team_subject)
    refuses it as team-unnamed / team-school. One row, two verdicts, the
    money reader holding the lower bar -- strict xfail until the two
    readers hold one bar (both sides: an abbreviation that is one of the
    codes AND a school)."""

    def test_the_other_side_with_an_abbreviation_alone(self, armed):
        mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", {"abbreviation": "wisc"}, TEAM_ND)
        assert map_lane.aec_code_side(WN, "Notre Dame", mk, 1)[1] == "side_code_unmatched"
        assert map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")[0] == "unverified"

    def test_the_other_side_with_a_school_alone(self, armed):
        mk = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", {"safeName": "Wisconsin"}, TEAM_ND)
        assert map_lane.grammar_truth(CON_ND, mk, 1, code="nd", school="Notre Dame")[0] == "unverified"

    def test_the_spread_reader_refuses_the_same_rows(self, armed):
        # the bar the spread reader holds today, pinned so the two stay comparable
        for other in ({"abbreviation": "lou"}, {"safeName": "Louisville"}):
            aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", other, TEAM_MISS)
            ex = _explain(_board(_venue_lm(aec)), f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
            assert ex["split"] == "spread:subject-uncertified", other
