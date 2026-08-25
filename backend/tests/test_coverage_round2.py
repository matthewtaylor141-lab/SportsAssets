"""Coverage the fleet found, with the mechanism verified in each case.

A six-lens fleet re-attacked the path from a detected whale fill to a
placed order and had to SIZE every proposal in dollars per day. Its
top finding was the missing `signed` column, which had already been
fixed by then — independent confirmation that the biggest one was real.

These are the next four. All are coverage-only: they let a copy be
matched that was previously refused, and none of them relaxes a check.
"""

import pytest

from sportsassets.copy_sports import market_type_of as mt
from sportsassets.workers.premap import slug_lines


class TestTheSpelledVocabulary:
    """Word-form tests are exact token membership, so a word one letter
    from a handled one falls to the >4-character unknown guard — and
    PREFIX_FOR_TYPE has no entry for "unknown", so resolve refuses
    before any matcher runs. Precisely the omission fixed this morning
    for 'spread', in the other direction."""

    @pytest.mark.parametrize("slug,want", [
        ("x-y-2026-08-25-totals-2pt5", "total"),
        ("x-y-2026-08-25-spreads-3pt5", "spread"),
        ("x-y-2026-08-25-handicaps-3pt5", "spread"),
        ("x-y-2026-08-25-moneyline-home", "moneyline"),
        ("x-y-2026-08-25-money-line-kc", "moneyline"),
        ("x-y-2026-08-25-h2h-ars", "moneyline"),
        ("x-y-2026-08-25-1x2-ars", "moneyline"),
        ("x-y-2026-08-25-both-teams-to-score-yes", "btts"),
    ])
    def test_the_plural_and_spelled_forms_type_correctly(self, slug, want):
        assert mt(slug) == want

    def test_tg_is_a_TOTAL_not_a_spread(self):
        """The venue's own total-games token fell to the bare-line
        fallback and typed a tennis TOTAL as a SPREAD. That is not a
        refusal, it is a wrong-MARKET route."""
        assert mt("atp-x-y-2026-08-25-tg-22pt5") == "total"

    def test_a_TEAM_total_is_still_a_prop_in_the_plural_too(self):
        assert mt("x-y-2026-08-25-team-totals-home-1pt5") == "prop"

    def test_ambiguous_propositions_stay_unknown(self):
        """'winner' could be set or series winner; draw-no-bet and
        double-chance are different bets. Unknown must stay unknown —
        the point of the type gate is that it fails closed."""
        for s in ("x-y-2026-08-25-winner", "x-y-2026-08-25-draw-no-bet",
                  "x-y-2026-08-25-double-chance",
                  "x-y-2026-08-25-margin-of-victory"):
            assert mt(s) == "unknown", s

    def test_moneylines_and_kind_prefixes_are_untouched(self):
        assert mt("mlb-nyy-bos-2026-07-22") == "moneyline"
        assert mt("mlb-nyy-bos-2026-07-22-nyy") == "moneyline"
        assert mt("asc-nfl-kc-buf-2026-08-25-kc") == "spread"

    def test_the_policy_twin_is_in_sync(self):
        from pathlib import Path

        import sportsassets.copy_sports as cs

        twin = (Path(cs.__file__).resolve().parents[2]
                / "edge-engine" / "src" / "edge" / "shadow"
                / "copy_sports.py")
        if not twin.exists():
            pytest.skip("sibling tree not present")
        assert twin.read_bytes() == Path(cs.__file__).read_bytes()


class TestWholeNumberLines:
    """slug_lines required a `pt` decimal marker or a leading o/u, so a
    whole-number line stated in the feed's own grammar decoded to
    nothing — and an empty his_lines makes line_ok refuse every row.

    _lines_of learned this exact lesson on 2026-08-24 ('only \\d+\\.5 was
    matched before, so a whole-number line produced NO line at all')
    and the decision was not carried across when slug_lines was written
    twelve hours ago. Failure mode (d), by me, same day."""

    @pytest.mark.parametrize("slug,want", [
        ("nba-bos-mia-2026-08-24-bos-neg-10", {"10"}),
        ("wnba-dal-wsh-2026-08-05-3", {"3"}),
        ("spl-ett-nsr-2026-08-25-total-3", {"3"}),
        ("nfl-kc-buf-2026-09-13-spread-away-3", {"3"}),
    ])
    def test_whole_numbers_decode(self, slug, want):
        assert slug_lines(slug) == want

    def test_half_point_lines_still_decode(self):
        assert slug_lines("ucl-sf-hbs-2026-08-25-total-4pt5") == {"4.5"}

    def test_a_moneyline_still_has_no_line(self):
        for s in ("mlb-nyy-bos-2026-07-22", "mlb-nyy-bos-2026-07-22-nyy",
                  "atc-alsv-mal-dju-2026-08-24-mal"):
            assert slug_lines(s) == set(), s

    def test_the_DATE_can_never_become_a_line(self):
        """It walks the POST-DATE tokens only. Reading the raw slug
        would make 2026, 08 and 25 all candidate lines."""
        import inspect

        from sportsassets.workers import premap

        src = inspect.getsource(premap.slug_lines)
        assert "_post_date_tokens" in src
        for s in ("nba-bos-mia-2026-08-24-bos-neg-10",
                  "spl-ett-nsr-2026-08-25-total-3"):
            assert not ({"2026", "8", "24", "25"} & slug_lines(s)), s


class TestTheEventTitleKeyLane:
    """premap.resolve builds keys from THREE sources — market title,
    event title, and the slug. event_title was None on every live copy,
    so one third of the key construction produced nothing every time.

    Two independent causes: `trades` has no event_title column and the
    sweep never selected one from markets; and on the live lane the
    only assignment is inside _enrich, which is spawned one line AFTER
    execute_copy, so the copy path wins the race and reads nothing."""

    def test_the_sweep_selects_it(self):
        import inspect

        from sportsassets.workers import copy_sweep as cs

        src = inspect.getsource(cs)
        assert "m.event_title" in src
        assert '"event_title": r["event_title"]' in src

    def test_the_sweep_pays_no_extra_query_for_it(self):
        """The markets join was already there for the resolved filter."""
        import inspect

        from sportsassets.workers import copy_sweep as cs

        src = inspect.getsource(cs)
        assert src.count("LEFT JOIN markets m") == 1

    def test_the_context_short_circuit_requires_a_title(self):
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le._market_context)
        assert 'ctx.get("event_title") or ctx.get("market_title")' in src

    def test_a_payload_with_no_title_falls_through_to_the_lookup(self):
        import asyncio

        from sportsassets import live_executor as le

        seen = {}

        class _P:
            async def fetchrow(self, _sql, *a):
                seen["asked"] = True
                return {"market_slug": "s", "event_slug": "e",
                        "market_title": "T", "event_title": "ET",
                        "outcome": "Yes"}

        ctx = asyncio.run(le._market_context(
            _P(), {"asset": "1", "market_slug": "s", "outcome": "Yes"}))
        assert seen.get("asked"), "it short-circuited without a title"
        assert ctx["event_title"] == "ET"

    def test_a_complete_payload_still_short_circuits(self):
        import asyncio

        from sportsassets import live_executor as le

        class _P:
            async def fetchrow(self, *_a):
                raise AssertionError("must not query when ctx is complete")

        ctx = asyncio.run(le._market_context(
            _P(), {"asset": "1", "market_slug": "s", "outcome": "Yes",
                   "market_title": "T"}))
        assert ctx["market_title"] == "T"


class TestTitleKeysCanActuallyMeet:
    """pmus._norm replaces each punctuation RUN with a space and never
    collapses, so the two sides of one game disagreed on SPACING:

        "Arsenal vs. Chelsea" -> "arsenal vs  chelsea"   (two spaces)
        "Arsenal vs Chelsea"  -> "arsenal vs chelsea"

    Two distinct keys, one game, and the deterministic lane could not
    intersect them. Abbreviations were worse: "Inter Miami C.F."
    became "inter miami c f" against "inter miami cf" — completely
    disjoint key sets for the same club."""

    def _keys(self, title, slug):
        from sportsassets.workers.premap import event_keys_for

        return set(event_keys_for(title, slug))

    def test_the_punctuated_and_bare_spellings_now_intersect(self):
        a = self._keys("Arsenal vs. Chelsea", "epl-ars-che-2026-08-25")
        b = self._keys("Arsenal vs Chelsea", "atc-epl-ars-che-2026-08-25")
        assert "arsenal vs chelsea" in (a & b)
        assert "arsenal vs chelsea@2026-08-25" in (a & b)

    def test_abbreviations_reach_the_same_string(self):
        a = self._keys("Inter Miami C.F. vs. Orlando City S.C.",
                       "mls-mia-orl-2026-08-25")
        b = self._keys("Inter Miami CF vs Orlando City SC",
                       "atc-mls-mia-orl-2026-08-25")
        assert "inter miami cf vs orlando city sc" in (a & b)

    def test_the_reversed_matchup_is_still_emitted(self):
        a = self._keys("Arsenal vs. Chelsea", "epl-ars-che-2026-08-25")
        assert "chelsea vs arsenal" in a

    def test_an_apostrophe_surname_normalizes(self):
        a = self._keys("Christopher O'Connell vs. Mai Ito",
                       "atp-oco-ito-2026-08-25")
        b = self._keys("Christopher OConnell vs Mai Ito",
                       "aec-atp-oco-ito-2026-08-25")
        assert a & b, "an apostrophe must not split one player in two"

    def test_pmus_norm_ITSELF_is_untouched(self):
        """It also produces side_norm, which is half of the us_premap
        unique index and half of match_side's equality test. Changing
        it would silently rewrite what counts as the same SIDE — the
        wrong-side incident's own machinery. Keys are a lookup, sides
        are a decision, and only the lookup is widened."""
        from sportsassets import pmus

        assert pmus._norm("Arsenal vs. Chelsea") == "arsenal vs  chelsea"

    def test_the_normalizer_is_local_to_key_building(self):
        import inspect

        from sportsassets.workers import premap

        assert "_key_norm" in inspect.getsource(premap.event_keys_for)
        assert "_key_norm" not in inspect.getsource(premap.match_side)


class TestTheSweepCanSeeItsOwnTruncation:
    """The page loop exits on a short page (board exhausted) or by
    running out of budget (board TRUNCATED), and the summary could not
    tell those apart — it published `events` and `rows`, which read as
    a large healthy sweep either way. A truncated board is markets that
    can NEVER be premapped, and premap is the only lane allowed to
    trade under the quarantine."""

    def test_the_summary_carries_both_facts(self):
        import inspect

        from sportsassets.workers import premap

        src = inspect.getsource(premap.refresh)
        assert '"pages_walked"' in src
        assert '"truncated"' in src

    def test_truncated_requires_BOTH_budget_and_a_full_last_page(self):
        """Exhausting the budget on a short final page is a complete
        sweep, not a truncated one."""
        import inspect

        from sportsassets.workers import premap

        src = inspect.getsource(premap.refresh)
        assert 'last_page_full' in src and 'pages_walked") == max_pages' in src

    def test_the_note_says_what_truncation_costs(self):
        import inspect

        from sportsassets.workers import premap

        assert "cannot be resolved at all" in inspect.getsource(premap.refresh)

    def test_the_caps_rose_and_the_pacing_did_not(self):
        from sportsassets.workers import premap

        assert premap.MAX_EVENT_PAGES >= 120
        assert premap.FAST_MAX_PAGES >= 25
        assert premap.LIST_PACING_S == 0.35, \
            "the pacing that fixed the 2026-08-23 429s must not move"
