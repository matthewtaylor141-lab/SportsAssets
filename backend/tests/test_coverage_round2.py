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
