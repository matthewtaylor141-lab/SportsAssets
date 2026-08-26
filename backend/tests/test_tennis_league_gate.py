"""Tennis never reached the exact resolver at all.

_tennis_candidates gated on head[0] -- the first segment of the whale's
slug -- and compared it against _TENNIS_LEAGUES. But the first segment
of one of these slugs is the KIND prefix (aec, atc, tsc, asc, cpc,
astatc); the league is the segment AFTER it. copy_sports.league_of has
always known that. This function carried a second, wrong copy of the
same decision -- the N-literals-one-decision failure, where N-1 got
updated.

    aec-atp-harwen-stetra-2026-08-24  ->  head[0]='aec'  ->  0 candidates
    atp-harwen-stetra-2026-08-24      ->  head[0]='atp'  ->  2 candidates

Only the second shape ever worked, and the feed does not produce it. So
resolve_market_exact was never called for tennis: every tennis copy fell
straight through to the fuzzy resolver, and fuzzy output is exactly what
the mapping quarantine refuses.

Tennis is 48% of the recent unmapped funnel -- 4,919 ATP, 2,425 WTA and
2,168 ITF rows in seven days. All of it died on one line.

The abbreviation grammar downstream was never the problem, and the tests
at the bottom prove it against eight venue slugs taken from real
overspend receipts.
"""

from __future__ import annotations

import pytest

from sportsassets.copy_sports import _KINDS, league_of
from sportsassets.live_executor import (_abbrev_player, _TENNIS_LEAGUES,
                                        _tennis_candidates)

TITLE = "Harry Wendelken vs. Stefano Travaglia"


class TestTheKindPrefixNoLongerBlindsIt:
    def test_a_real_venue_shaped_tennis_slug_yields_candidates(self):
        c = _tennis_candidates(TITLE, "aec-atp-harwen-stetra-2026-08-24")
        assert c, "the shape the feed actually produces yielded nothing"

    def test_the_first_candidate_is_the_real_venue_slug(self):
        """Taken verbatim from a production overspend receipt, so this
        is a slug the venue is known to serve."""
        c = _tennis_candidates(TITLE, "aec-atp-harwen-stetra-2026-08-24")
        assert c[0] == "aec-atp-harwen-stetra-2026-08-24"

    def test_both_player_orders_are_still_generated(self):
        """Home/away order is the venue's choice, not the title's."""
        c = _tennis_candidates(TITLE, "aec-atp-harwen-stetra-2026-08-24")
        assert "aec-atp-stetra-harwen-2026-08-24" in c

    def test_a_slug_with_no_kind_prefix_still_works(self):
        """The shape that used to be the only one accepted must not
        regress."""
        assert _tennis_candidates(TITLE, "atp-harwen-stetra-2026-08-24")

    @pytest.mark.parametrize("kind", sorted(_KINDS))
    def test_every_kind_prefix_is_seen_through(self, kind):
        c = _tennis_candidates(TITLE, f"{kind}-wta-a-b-2026-08-26")
        assert c, f"{kind}- still blinds the tennis gate"

    def test_it_uses_league_of_rather_than_restating_the_rule(self):
        """One definition. A second copy is how this broke."""
        import inspect

        # CODE only -- the comment above the fix quotes head[0] on
        # purpose, to record what the line used to say.
        src = inspect.getsource(_tennis_candidates)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert "league_of" in code
        assert "head[0]" not in code


class TestItStillRefusesEverythingItShould:
    @pytest.mark.parametrize("slug", [
        "aec-mlb-min-ath-2026-08-26",
        "aec-cs2-nem-1win-2026-08-26",
        "atc-arg2-fco-abo-2026-08-22-fco",
        "tsc-ucl-lask-cel-2026-08-25-2pt5",
    ])
    def test_a_non_tennis_league_yields_nothing(self, slug):
        assert _tennis_candidates(TITLE, slug) == []

    def test_a_slug_with_no_date_yields_nothing(self):
        assert _tennis_candidates(TITLE, "aec-atp-harwen-stetra") == []

    def test_doubles_still_refuse_outright(self):
        """'A / B vs C / D' has no singles grammar, and a fabricated
        token is a live probe into the 6-char slug space."""
        c = _tennis_candidates("Adam Smith / Ben Jones vs Carl Lee / Dan Kim",
                               "aec-atp-a-b-2026-08-26")
        assert c == []

    def test_an_unparseable_title_yields_nothing(self):
        assert _tennis_candidates("no versus here at all",
                                  "aec-atp-a-b-2026-08-26") == []

    def test_a_single_token_player_name_refuses(self):
        assert _tennis_candidates("Ito vs Sakamoto",
                                  "aec-atp-a-b-2026-08-26") == []

    def test_identical_abbreviations_refuse(self):
        """Both players collapsing to one token would generate a slug
        naming the same player twice."""
        assert _tennis_candidates("Jan Choinski vs Jan Choinski",
                                  "aec-atp-a-b-2026-08-26") == []


class TestTheITFTourHintSurvives:
    def test_itf_still_enumerates_both_tours(self):
        c = _tennis_candidates(TITLE, "aec-itf-a-b-2026-08-26")
        assert any("itfwo" in x for x in c)
        assert any("itfme" in x for x in c)

    def test_a_mens_title_puts_the_mens_code_first(self):
        c = _tennis_candidates("ITF M25 Taipei: Adam Smith vs Ben Jones",
                               "aec-itf-a-b-2026-08-26")
        assert c and "itfme" in c[0]

    def test_an_explicit_tour_league_is_not_re_enumerated(self):
        c = _tennis_candidates(TITLE, "aec-itfme-a-b-2026-08-26")
        assert all("itfwo" not in x for x in c)


class TestTheGrammarDownstreamWasNeverTheProblem:
    """Eight venue slugs from real production overspend receipts. If the
    abbreviation rule were wrong these would not round-trip and the
    investigation would have started somewhere else."""

    @pytest.mark.parametrize("name,token", [
        ("Harry Wendelken", "harwen"), ("Stefano Travaglia", "stetra"),
        ("Daniel Altmaier", "danalt"), ("Francisco Comesana", "fracom"),
        ("Coleman Wong", "colwon"), ("Elmer Moeller", "elmmoe"),
        ("Dominika Salkova", "domsal"), ("Ekaterina Ovcharenko", "ekaovc"),
    ])
    def test_the_abbreviation_matches_the_venue(self, name, token):
        assert _abbrev_player(name) == token

    def test_league_of_and_the_gate_now_agree(self):
        for slug in ("aec-atp-a-b-2026-08-26", "aec-wta-a-b-2026-08-26",
                     "aec-itfme-a-b-2026-08-26", "aec-chal-a-b-2026-08-26"):
            assert league_of(slug) in _TENNIS_LEAGUES
            assert _tennis_candidates(TITLE, slug)
