"""Alternate tennis spreads exist on the venue and not on our desk.

Owner 2026-08-26, with a screenshot of a live tennis match showing
Spread (alternate lines 5.5 / 2.5 / 1.5), Exact Match Score, Set Winner
and Total Games: "we have alternate spreads in tennis on the Kalshi over
the counter desk, currently not an option (especially for live matches),
and the actual Kalshi platform has the market, our otc desk does not."

Census ground truth, run on a GitHub runner because this container's
network policy blocks the venue -- 3,516 sports series read from
/series?category=Sports:

  * KXATPMATCH and KXWTAMATCH -- the ONLY two tennis series the desk
    browsed -- reported ZERO open events, while KXATPCHALLENGERMATCH
    (9), KXATPCHALLENGERDOUBLES (12), KXATPDOUBLES (4) and KXWTADOUBLES
    (4) were live. The tennis board was empty at the source.
  * The spread and total families are real and were never asked for:
    KXATPGAMESPREAD, KXATPGSPREAD, KXATPSSPREAD, KXATPGAMETOTAL,
    KXATPGTOTAL, KXATPTOTALSETS, KXWTAGTOTAL.
  * SETWINNER / GWINNER / EXACTMATCH -- which I first reported as wrong
    guesses -- are correct. KXATPSETWINNER alone had 70 open events.
    That reading of mine was wrong and this file records it, because a
    wrong diagnosis left standing is how the next person re-fixes
    something that already worked.

One caveat the census also forced, kept here so nobody treats the
numbers above as final: /events?status=open&limit=200 returned length 0
for KXWTACHALLENGERMATCH while the SAME series with
with_nested_markets=true returned a live event. The venue's open-event
count is not reliable evidence of an empty board, so nothing here
concludes a series is dead from a zero.
"""

from __future__ import annotations

import inspect

from sportsassets.api import app as app_mod


class TestOneListNotThree:
    def test_the_browse_list_is_built_from_the_tennis_list(self):
        assert set(app_mod._TENNIS_MATCH_SERIES) <= set(
            app_mod._DESK_KALSHI_SERIES)

    def test_no_call_site_respells_the_pair(self):
        """This was the same decision written in THREE places -- the
        browse constant and a series_by_league map in each of desk-games
        and desk-feed. A fix that updated some of them would look like
        it worked."""
        src = inspect.getsource(app_mod)
        body = src[src.index("_TENNIS_MATCH_SERIES = ["):]
        body = body[body.index("]"):]
        assert '"tennis": ["KXATPMATCH"' not in body
        assert body.count('list(_TENNIS_MATCH_SERIES)') >= 2

    def test_the_tour_boards_are_still_browsed(self):
        """Zero open events today is not proof of a dead series -- see
        the module docstring's caveat. Adding boards must not drop
        any."""
        for s in ("KXATPMATCH", "KXWTAMATCH"):
            assert s in app_mod._TENNIS_MATCH_SERIES

    def test_the_live_boards_were_added(self):
        for s in ("KXATPCHALLENGERMATCH", "KXATPCHALLENGERDOUBLES",
                  "KXATPDOUBLES", "KXWTADOUBLES"):
            assert s in app_mod._TENNIS_MATCH_SERIES


class TestTheCloseWindow:
    def test_every_tennis_board_fetches_unwindowed(self):
        """The venue stamps a tennis market with the TOURNAMENT's close
        (Sep 6 for an Aug 26 match), so a game-time window structurally
        hides the board. The old test was a startswith on two of the
        members -- and KXATPCHALLENGERMATCH does not start with
        KXATPMATCH, so every newly added board would have been handed
        the 7-day game-sport window this branch exists to avoid."""
        src = inspect.getsource(app_mod._kalshi_board_sweep)
        assert "in _TENNIS_MATCH_SERIES" in src
        assert 'startswith(("KXATPMATCH"' not in src

    def test_the_membership_test_actually_covers_them(self):
        src = inspect.getsource(app_mod._kalshi_board_sweep)
        ns = {"_TENNIS_MATCH_SERIES": app_mod._TENNIS_MATCH_SERIES}
        exec(compile(  # noqa: S102 -- the real line, run on real input
            "tennis = [x for x in series_list if x in "
            "_TENNIS_MATCH_SERIES]", "<f>", "exec"),
            ns, ns2 := {"series_list": app_mod._DESK_KALSHI_SERIES,
                        **ns})
        assert set(ns2["tennis"]) == set(app_mod._TENNIS_MATCH_SERIES)
        assert "KXMLBGAME" not in ns2["tennis"]


class TestTheSiblingSweep:
    def _src(self) -> str:
        return inspect.getsource(app_mod.api_desk_game)

    def test_spread_and_total_families_are_requested(self):
        """The whole ask. The MATCH sibling list carried set/exact-score
        families only; SPREAD and TOTAL were never fetched, so the group
        could not render however the venue quoted it."""
        for suf in ("GAMESPREAD", "GSPREAD", "SSPREAD",
                    "GAMETOTAL", "GTOTAL", "TOTALSETS"):
            assert suf in app_mod._TENNIS_SIBLING_SUFFIXES

    def test_both_venue_spellings_are_asked_for(self):
        """GAMESPREAD and GSPREAD are two separate real series on the
        venue, not a typo for one another; same for GAMETOTAL/GTOTAL."""
        s = app_mod._TENNIS_SIBLING_SUFFIXES
        assert "GAMESPREAD" in s and "GSPREAD" in s
        assert "GAMETOTAL" in s and "GTOTAL" in s

    def test_the_families_that_already_worked_are_kept(self):
        """My first diagnosis said these were wrong names. The census
        says otherwise -- KXATPSETWINNER had 70 open events. Removing
        them on a wrong reading would have broken a working group."""
        for suf in ("SETWINNER", "GWINNER", "EXACTMATCH"):
            assert suf in app_mod._TENNIS_SIBLING_SUFFIXES

    def test_a_challenger_board_reaches_the_tour_stem(self):
        """KXATPCHALLENGERMATCH stems to KXATPCHALLENGER, and no
        derivative series is named off that. Without the tour stem a
        challenger match shows no Spreads at all."""
        src = self._src()
        assert 'for tour in ("KXATP", "KXWTA")' in src
        assert "stems.add(tour)" in src
        # and the real derivation, run on the real input
        series0 = "KXATPCHALLENGERMATCH"
        stem = series0[: -len("MATCH")]
        stems = {stem} | {t for t in ("KXATP", "KXWTA")
                          if series0.startswith(t)}
        sibs = [st + suf for st in sorted(stems)
                for suf in app_mod._TENNIS_SIBLING_SUFFIXES]
        assert "KXATPGAMESPREAD" in sibs
        assert "KXATPSETWINNER" in sibs

    def test_the_tour_board_still_derives_its_own_siblings(self):
        series0 = "KXATPMATCH"
        stem = series0[: -len("MATCH")]
        assert stem == "KXATP"
        assert stem + "GAMESPREAD" == "KXATPGAMESPREAD"


class TestGrouping:
    """Calls production's ladder. An earlier draft of this class carried
    its OWN copy of the if-chain and compared the two by string, which
    grades the test's arithmetic rather than the code's -- the failure
    that let a mutated build pass this morning. _kalshi_group_label
    exists so there is one ladder and the tests can reach it."""

    def _label_of(self, series: str) -> str:
        return app_mod._kalshi_group_label(series)

    def test_spreads_and_totals_get_their_own_groups(self):
        assert self._label_of("KXATPGAMESPREAD") == "Spreads"
        assert self._label_of("KXATPSSPREAD") == "Spreads"
        assert self._label_of("KXATPGAMETOTAL") == "Totals"
        assert self._label_of("KXATPTOTALSETS") == "Totals"

    def test_exact_match_is_not_swallowed_by_the_moneyline_test(self):
        """KXATPEXACTMATCH ends in MATCH. Deciding Moneyline first would
        file every exact-score row under the winner group."""
        assert self._label_of("KXATPEXACTMATCH") == "Exact Score"
        assert self._label_of("KXATPMATCH") == "Moneyline"
        assert self._label_of("KXATPCHALLENGERMATCH") == "Moneyline"

    def test_the_set_and_prop_families_still_land(self):
        assert self._label_of("KXATPSETWINNER") == "Set Winners"
        assert self._label_of("KXATPANYSET") == "Set Winners"
        assert self._label_of("KXATPGWINNER") == "Game Props"
        assert self._label_of("KXATPTIEBREAK") == "Game Props"

    def test_every_suffix_we_fetch_lands_somewhere_nameable(self):
        """A family we ask the venue for and then file under 'More' is a
        group the desk renders without a name."""
        for suf in app_mod._TENNIS_SIBLING_SUFFIXES:
            assert self._label_of("KXATP" + suf) != "More", suf
