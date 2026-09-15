"""A dateless signal could reach another date's game — and I armed it.

premap.resolve builds keys as:

    d = date_of(global_slug)
    event_keys_for(t, global_slug if d else None)
    ... and _dated_admissible is applied only `if d`

So when the whale's slug carries no date, the key set is BARE TITLES
with no date stamp, and the dated-admissibility filter never runs. A
bare "tigre vs cacique" matches the venue's row for that pairing on
ANY date — the 2026-08-24 game and the 2026-09-14 game alike.

IT WAS LATENT AND THE event_title FIX ARMED IT. Until that change, a
dateless signal produced only {market_title} — "tigre" — which
intersected nothing, because venue rows are keyed off EVENT titles.
Supplying event_title turned "matches nothing" into "may match the
wrong date's game".

Found by the coverage fleet's own adversarial verifiers, rejecting the
proposal I had already shipped. That is the whole value of the pass:
its top-ranked finding was one I had fixed, and its rejection notes
caught what the fix broke.

Refusing costs nothing that was ever safe: with no date, no slug key is
built either, so bare titles were the ONLY keys such a signal had.
"""

import inspect

import pytest

from sportsassets.workers import premap


class TestTheHazardIsReal:
    def test_a_dateless_slug_yields_no_date(self):
        assert not premap.date_of("lpa-tig-cac-tig")

    def test_bare_title_keys_carry_no_game_identity(self):
        """The keys themselves — nothing here says WHICH Tigre vs
        Cacique, so they match every one the venue has ever listed."""
        keys = set(premap.event_keys_for("Tigre vs Cacique", None))
        assert "tigre vs cacique" in keys
        assert not any("@" in k for k in keys)

    def test_a_DATED_signal_stamps_every_title_key(self):
        keys = set(premap.event_keys_for("Tigre vs Cacique",
                                         "lpa-tig-cac-2026-08-24-tig"))
        assert "tigre vs cacique@2026-08-24" in keys


class TestBothResolversRefuse:
    def test_resolve_returns_none_without_a_date(self):
        src = inspect.getsource(premap.resolve)
        i = src.index("d = date_of(global_slug)")
        assert "if not d:\n        return None" in src[i:i + 200]

    def test_the_refusal_precedes_any_key_building(self):
        src = inspect.getsource(premap.resolve)
        assert src.index("if not d:") < src.index("event_keys_for(")

    def test_the_census_names_the_step(self):
        """A silent refusal would move the whole class into an
        unattributed bucket, and the census is what coverage work is
        prioritised from."""
        src = inspect.getsource(premap.resolve_explain)
        assert '"no_date_on_his_signal"' in src
        assert "match that pairing on every date" in src

    def test_the_comment_records_that_the_fix_armed_it(self):
        src = inspect.getsource(premap.resolve)
        assert "I ARMED IT" in src


class TestTheSweepStoppedSwappingSideSelectionInputs:
    """market_title feeds match_side's yes/no question agreement and
    its line extraction; market_slug is the global_slug that date_of
    and slug_lines read. Sourcing either from `markets` instead of
    `trades` silently changes SIDE and GAME selection wherever the two
    disagree — not the coverage-only change I labelled it."""

    def _src(self):
        from sportsassets.workers import copy_sweep as cs

        return inspect.getsource(cs)

    def test_only_event_title_comes_from_markets(self):
        src = self._src()
        assert "m.event_title" in src
        assert "COALESCE(t.market_title, m.title)" not in src
        assert "COALESCE(t.market_slug, m.slug)" not in src
        assert "COALESCE(t.event_slug, m.event_slug)" not in src

    def test_the_trades_columns_are_used_directly(self):
        src = self._src()
        for col in ("t.market_title", "t.market_slug", "t.event_slug"):
            assert col in src, col

    def test_the_event_title_gain_is_kept(self):
        src = self._src()
        assert '"event_title": r["event_title"]' in src
