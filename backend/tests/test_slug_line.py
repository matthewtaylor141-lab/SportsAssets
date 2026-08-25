"""Not one totals copy in the feed could match, and totals are not hard.

The census printed the whole defect in one line:

    UNMAPEG no_side_match: his=ucl-sf-hbs-2026-08-25-total-4pt5
      outcome=Over keys=5 premap_rows=20
      | outcome 'Over' matched none of ['over','under','over','under',…]

The premap had the market, both sides, and the right words. He picked
"Over". The rows say "over". They did not match because an over/under
pick is meaningless without its line — correctly — so match_side
requires both sides to state one. His line is not in his outcome text
("Over") and not in the market title. It is in his slug: `total-4pt5`.

_lines_of never saw the slug, so his_lines came back empty and line_ok
refused every row. no_side_match is 93 of 400 sampled misses (23.3%).

Beside it, one word apart, the same omission in market_type_of: the
spelled form 'total' was handled and the spelled form 'spread' was not,
so `spl-sha-riy-2026-08-25-spread-away-1pt5` returned "unknown" — and
unknown is never tradeable.

THIS TIGHTENS THE GUARD. The requirement that the lines agree is
untouched; the line the whale actually stated is supplied instead of
its absence being treated as unknowable. A disagreeing line still
refuses, now for the right reason.
"""

import pytest

from sportsassets.copy_sports import market_type_of
from sportsassets.workers import premap
from sportsassets.workers.premap import match_side, slug_lines


class TestTheLineIsDecodedFromTheSlug:
    @pytest.mark.parametrize("slug,want", [
        ("ucl-sf-hbs-2026-08-25-total-4pt5", {"4.5"}),
        ("spl-ett-nsr-2026-08-25-total-1pt5", {"1.5"}),
        ("spl-sha-riy-2026-08-25-spread-away-1pt5", {"1.5"}),
        ("mlb-nyy-bos-2026-07-22-o8pt5", {"8.5"}),
        ("nba-x-y-2026-08-25-u10", {"10"}),
    ])
    def test_both_encodings(self, slug, want):
        assert slug_lines(slug) == want

    def test_the_format_matches_what_the_venue_rows_carry(self):
        """A row's `line` is stamped by _lines_of, so a decoded line
        that formats differently would compare unequal and refuse just
        as silently as before."""
        assert slug_lines("x-y-2026-08-25-total-4pt5") <= \
            premap._lines_of("Total Goals 4.5")


class TestAMoneylineCanNeverAcquireALine:
    """The regression this could have caused: a stray digit pattern
    turning a currently-matching UNLINED pick into a refusal."""

    @pytest.mark.parametrize("slug", [
        "mlb-nyy-bos-2026-07-22",
        "mlb-nyy-bos-2026-07-22-nyy",
        "atc-alsv-mal-dju-2026-08-24-mal",
        "aec-npb-cdh-hta-2026-08-27",
        "",
        None,
    ])
    def test_no_line_is_invented(self, slug):
        assert slug_lines(slug) == set()

    def test_the_gate_is_the_market_TYPE_not_a_pattern(self):
        import inspect

        src = inspect.getsource(slug_lines)
        assert "market_type_of" in src
        assert "_LINED_TYPES" in src


class TestTheTotalsMatchThatWasRefused:
    def _rows(self, line="4.5"):
        return [{"identifier": "tsc-ucl-sf-hbs-2026-08-25-over",
                 "side_norm": "over", "line": line, "kind": "side",
                 "question": "Total Goals"},
                {"identifier": "tsc-ucl-sf-hbs-2026-08-25-under",
                 "side_norm": "under", "line": line, "kind": "side",
                 "question": "Total Goals"}]

    def test_it_refused_before(self):
        assert match_side(self._rows(), "Over", "Total Goals") is None

    def test_it_matches_now(self):
        hit = match_side(self._rows(), "Over", "Total Goals",
                         "ucl-sf-hbs-2026-08-25-total-4pt5")
        assert hit is not None
        assert hit["identifier"].endswith("-over")

    def test_it_still_picks_the_side_he_picked(self):
        hit = match_side(self._rows(), "Under", "Total Goals",
                         "ucl-sf-hbs-2026-08-25-total-4pt5")
        assert hit["identifier"].endswith("-under")

    def test_A_DIFFERENT_LINE_STILL_REFUSES(self):
        """The whole point. Over 4.5 must never match an Over 9.5 row —
        that is a different bet, and it is the wrong-line class this
        guard was written for."""
        assert match_side(self._rows("9.5"), "Over", "Total Goals",
                          "ucl-sf-hbs-2026-08-25-total-4pt5") is None

    def test_an_unlined_row_still_refuses_a_lined_pick(self):
        assert match_side(self._rows(""), "Over", "Total Goals",
                          "ucl-sf-hbs-2026-08-25-total-4pt5") is None

    def test_ambiguity_still_refuses(self):
        rows = self._rows() + self._rows()
        assert match_side(rows, "Over", "Total Goals",
                          "ucl-sf-hbs-2026-08-25-total-4pt5") is None


class TestTheWordFormSpread:
    def test_it_was_unknown_and_unknown_is_never_tradeable(self):
        assert market_type_of("spl-sha-riy-2026-08-25-spread-away-1pt5") \
            == "spread"

    def test_handicap_reads_the_same(self):
        assert market_type_of("x-y-2026-08-25-handicap-home-2pt5") \
            == "spread"

    def test_the_spelled_total_is_unchanged(self):
        assert market_type_of("ucl-sf-hbs-2026-08-25-total-4pt5") == "total"

    def test_a_TEAM_total_is_still_a_prop_not_a_total(self):
        assert market_type_of("x-y-2026-08-25-team-total-home-1pt5") \
            == "prop"

    def test_moneylines_are_untouched(self):
        assert market_type_of("mlb-nyy-bos-2026-07-22") == "moneyline"
        assert market_type_of("mlb-nyy-bos-2026-07-22-nyy") == "moneyline"

    def test_kind_prefixed_venue_slugs_are_untouched(self):
        assert market_type_of("asc-nfl-kc-buf-2026-08-25-kc") == "spread"
        assert market_type_of("tsc-nfl-kc-buf-2026-08-25") == "total"

    def test_a_spread_without_a_sign_still_refuses_on_the_named_branch(self):
        """slug_lines supplies a MAGNITUDE and no sign, and _norm erases
        +/-. The sign check must still refuse rather than let the
        magnitude alone select a side — that inversion is the incident
        this codebase is named after."""
        rows = [{"identifier": "asc-x-kc",
                 "side_norm": premap._norm("Kansas City Chiefs -3.5"),
                 "line": "3.5",
                 "signed": premap.signed_line("Kansas City Chiefs -3.5"),
                 "kind": "side", "question": "Spread"}]
        assert match_side(rows, "Kansas City Chiefs", "Spread",
                          "nfl-kc-buf-2026-08-25-spread-away-3pt5") is None


class TestEveryMatcherGotTheSameArgument:
    """A verifier fed a different argument list than production is not
    verifying production."""

    def test_resolve_passes_the_slug(self):
        import inspect

        src = inspect.getsource(premap.resolve)
        assert "match_side(rows, outcome, market_title, global_slug)" in src

    def test_resolve_explain_passes_the_slug(self):
        import inspect

        src = inspect.getsource(premap.resolve_explain)
        assert "match_side(kept, outcome, market_title, global_slug)" in src

    def test_the_side_echo_passes_the_slug(self):
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le._side_echo_verify)
        assert "his_slug)" in src and "match_side(" in src

    def test_the_policy_twin_stayed_in_sync(self):
        from pathlib import Path

        import sportsassets.copy_sports as cs

        root = Path(cs.__file__).resolve().parents[2]
        twin = (root.parent / "edge-engine" / "src" / "edge" / "shadow"
                / "copy_sports.py")
        if not twin.exists():
            pytest.skip("sibling tree not present")
        assert twin.read_bytes() == Path(cs.__file__).read_bytes()
