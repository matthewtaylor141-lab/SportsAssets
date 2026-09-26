"""Soccer's book terms, from the run-67 capture, and only from it.

WHAT WAS WRONG BEFORE. `BOOK_TERMS` was keyed on baseball alone, so every
soccer settlement comparison returned UNKNOWN -- and UNKNOWN reads as "we
did not look", which is exactly what had happened: the terms extractor's
keyword vocabulary was baseball-only, so Pinnacle's soccer section had
never been read. The prose was captured in run 67 and then sat in a test
fixture, because at the time no soccer candidate existed to compare.

THE RULE THESE ENFORCE. Every payout class below must be traceable to a
quoted line of the capture, and every condition the soccer section does
NOT speak to must be ABSENT from the map rather than filled in. An absent
condition reads as unstated and blocks; a filled-in one reads as
agreement, which is the difference between a refusal and a false edge.
"""

import json
import pathlib

import pytest

from sportsassets import bettor_settlement_terms as T


FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "pinnacle_soccer_rules_2026_09_25.json")

SOCCER_KEYS = (("soccer", "h2h", T.CTX_PRE_GAME),
               ("soccer", "h2h", T.CTX_LIVE))


@pytest.fixture(scope="module")
def captured():
    return json.loads(FIXTURE.read_text())


# ── 1 · EVERY QUOTE IS THE CAPTURE'S, CHARACTER FOR CHARACTER ────────

def test_the_capture_exists_and_names_its_provenance(captured):
    assert captured["http_status"] == 200
    assert captured["source_url"].startswith("https://www.pinnacle.com")
    assert captured["retrieved_at"] == T._SOCCER_AT
    assert captured["captured_in"]["run"] == 67


@pytest.mark.parametrize("const,line", [
    ("_Q_SOCCER_90", "line_382"),
    ("_Q_SOCCER_VOID", "line_383"),
    ("_Q_SOCCER_85", "line_384"),
])
def test_each_quote_matches_the_captured_line_exactly(captured, const, line):
    """Not "contains" -- equal. A paraphrase is an interpretation."""
    assert getattr(T, const) == captured["soccer_section"][line]


def test_every_soccer_cite_carries_the_captures_retrieval_time(captured):
    for key in SOCCER_KEYS:
        for cond, spec in T.BOOK_TERMS[key].items():
            cite = spec["cite"]
            assert cite["retrieved_at"] == captured["retrieved_at"], (key, cond)
            assert cite["source_url"] == captured["source_url"], (key, cond)
            assert cite["quote"].strip(), (key, cond)


def test_every_soccer_quote_is_drawn_from_the_captured_text(captured):
    """A cite may concatenate captured lines; it may not add a new one."""
    pool = " ".join(list(captured["soccer_section"].values())
                    + [captured["general_rule_also_applicable"]["quote"]])
    for key in SOCCER_KEYS:
        for cond, spec in T.BOOK_TERMS[key].items():
            for part in spec["cite"]["quote"].split(". "):
                part = part.strip().rstrip(".")
                if len(part) < 25:
                    continue
                assert part in pool, (key, cond, part[:60])


# ── 2 · WHAT IS MAPPED, AND WHAT IS DELIBERATELY NOT ─────────────────

def test_the_four_conditions_the_prose_speaks_to_are_mapped():
    for key in SOCCER_KEYS:
        assert set(T.BOOK_TERMS[key]) == {
            T.C_FULL, T.C_OVERTIME, T.C_NOT_PLAYED, T.C_STOPPED_EARLY}, key


def test_the_three_conditions_it_is_silent_on_are_absent():
    """Silence must stay silence. A filled-in condition reads as agreement."""
    for key in SOCCER_KEYS:
        for cond in (T.C_CALLED_FINAL, T.C_SUSPENDED_RESUMED,
                     T.C_SUSPENDED_BEYOND):
            assert cond not in T.BOOK_TERMS[key], (key, cond)


def test_extra_time_does_not_change_the_payout_which_is_the_opposite_of_baseball():
    """Soccer grades on 90 minutes; baseball includes extra innings."""
    soccer = T.BOOK_TERMS[("soccer", "h2h", T.CTX_PRE_GAME)][T.C_OVERTIME]
    assert soccer["payout"] == T.PAY_ON_FINAL
    assert "does not include extra time" in soccer["cite"]["quote"]
    assert "Extra time" in soccer["note"]


def test_an_abandoned_match_returns_the_stake():
    for key in SOCCER_KEYS:
        spec = T.BOOK_TERMS[key][T.C_NOT_PLAYED]
        assert spec["payout"] == T.PAY_STAKE_BACK, key
        # NEVER the venue's rule. This is the conflict that blocks baseball
        # and it is present in soccer for the same reason.
        assert spec["payout"] != T.PAY_LAST_FAIR_MARKET_PRICE


def test_the_two_contexts_are_identical_because_the_prose_does_not_split_them():
    """Baseball splits on four numbered rules; the soccer section does not."""
    pre = {c: s["payout"] for c, s in
           T.BOOK_TERMS[("soccer", "h2h", T.CTX_PRE_GAME)].items()}
    live = {c: s["payout"] for c, s in
            T.BOOK_TERMS[("soccer", "h2h", T.CTX_LIVE)].items()}
    assert pre == live


# ── 3 · WHAT THE COMPARISON STILL DOES NOT COVER, DECLARED ───────────

def test_the_in_play_void_branches_outside_the_vocabulary_are_recorded():
    extra = T.SOCCER_IN_PLAY_EXTRA_VOID_BRANCHES
    assert "Video Assistant Referee" in extra["var_decision"]["quote"]
    assert "red card" in extra["incorrect_market_information"]["quote"]
    assert "outside this vocabulary" in extra["consequence"]


def test_the_in_play_void_branches_are_not_folded_into_the_terms():
    """Recording them as conditions would claim they are compared."""
    for key in SOCCER_KEYS:
        for cond in T.BOOK_TERMS[key]:
            assert "VAR" not in cond
            assert cond in T.CONDITIONS


def test_the_ninety_minute_basis_is_declared_as_a_payout_rule():
    basis = T.SOCCER_NINETY_MINUTE_BASIS
    assert basis["quote"] == T._Q_SOCCER_90
    assert "different event" in basis["consequence"]


def test_the_world_cup_window_is_an_excluded_phase_not_a_footnote():
    assert "72 hours" in T.SOCCER_WORLD_CUP_EXCEPTION["quote"]
    assert "outside this capture" in T.SOCCER_WORLD_CUP_EXCEPTION[
        "consequence"]


# ── 4 · THE SCOPE GATE, WHICH IS WHERE SOCCER NOW STOPS ──────────────

def test_soccer_has_a_declared_scope_so_the_refusal_names_the_real_gap():
    assert ("soccer", "h2h") in T.CAPTURED_SCOPE
    got = T.admit_scope(sport_family="soccer", market="h2h")
    assert got["ok"] is False
    # NOT "no capture for soccer" -- the rules ARE captured. What is
    # missing is the FIXTURE's phase and format, which is a different fix.
    assert T.R_SCOPE_UNDECLARED not in got["refusals"]
    assert T.R_PHASE_UNKNOWN in got["refusals"]
    assert T.R_FORMAT_UNKNOWN in got["refusals"]


def test_a_ninety_minute_regular_fixture_is_inside_the_capture():
    got = T.admit_scope(sport_family="soccer", market="h2h",
                        phase=T.PHASE_REGULAR, game_format=T.FMT_NINETY)
    assert got["ok"] is True, got


def test_a_knockout_tie_is_excluded_with_a_stated_reason():
    got = T.admit_scope(sport_family="soccer", market="h2h",
                        phase=T.PHASE_REGULAR, game_format=T.FMT_KNOCKOUT)
    assert got["ok"] is False
    assert T.R_FORMAT_EXCLUDED in got["refusals"]
    assert T.FMT_KNOCKOUT in T.SCOPE_NOTE


def test_a_nine_inning_format_cannot_be_used_to_admit_a_soccer_fixture():
    got = T.admit_scope(sport_family="soccer", market="h2h",
                        phase=T.PHASE_REGULAR, game_format=T.FMT_NINE)
    assert got["ok"] is False
    assert T.R_FORMAT_EXCLUDED in got["refusals"]


# ── 5 · BASEBALL IS UNTOUCHED ────────────────────────────────────────

def test_the_baseball_terms_and_scope_did_not_move():
    assert T.CAPTURED_SCOPE[("baseball", "h2h")] == {
        "phases": (T.PHASE_REGULAR,), "formats": (T.FMT_NINE,)}
    pre = T.BOOK_TERMS[("baseball", "h2h", T.CTX_PRE_GAME)]
    live = T.BOOK_TERMS[("baseball", "h2h", T.CTX_LIVE)]
    assert set(pre) == set(T.CONDITIONS)
    assert set(live) == set(T.CONDITIONS)
    # The divergence the baseball capture exists to record.
    assert pre[T.C_CALLED_FINAL]["payout"] == T.PAY_ON_PARTIAL_WALKOFF
    assert live[T.C_CALLED_FINAL]["payout"] == T.PAY_STAKE_BACK


def test_no_book_terms_key_claims_a_market_other_than_h2h():
    for family, market, ctx in T.BOOK_TERMS:
        assert market == "h2h", (family, market)
        assert ctx in (T.CTX_PRE_GAME, T.CTX_LIVE), ctx
