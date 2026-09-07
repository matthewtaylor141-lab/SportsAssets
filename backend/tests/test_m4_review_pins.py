"""M4 review pins (2026-09-07): the map-winner pick against the twins
the builder's board does not carry, the wording arm's blindness to the
astatc map rows stated as a spy, the date guard exercised with the keys
bypassed, and the total-maps route on both switch states.

Rows are the builder's fixtures (test_m4_esports: the nf-venue_1350 /
esports_chi_rows_1403 rows verbatim) plus the twin rows each test names.
Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio

import pytest

from sportsassets.workers import premap

from tests.test_m4_esports import (D, LONG, T_FNC_NIP, _Pool, _board, _map, _map_ex,
                                   _resolve, _short, _yn)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


class TestTheMapPickNeverLeavesHisLeagueCode:
    """The pick has no league-code witness: every candidate reached
    through his event title's matchup keys ('fnatic vs nip@date') is
    read whatever league code its identifier carries. fnatic and NIP
    field rosters in several games; a venue twin under another code on
    the same day, listing the map his cs2 event does not (hero-1win
    listed map2 and not map1 at 13:50Z), is then his only candidate."""

    def test_a_same_day_twin_under_another_league_code_is_never_the_row(self, armed):
        twin = f"aec-val-fnc-nip-{D}"
        extra = [(twin, _yn(f"astatc-val-fnc-nip-{D}-map1", "Will NIP win Map 1 vs fnatic?"))]
        rows = _board(extra=extra, drop=(f"astatc-cs2-fnc-nip-{D}-map1",),
                      events={twin: "fnatic vs. NIP"})
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        ex = _map_ex(rows, "fnc-nip", 1, "NIP")
        assert ex["step"] != "resolves"
        assert str(ex.get("split", "")).startswith("map:")
        # and when both codes list the map, the cs2 row alone is his --
        # never ambiguity against a code that is not his slug's
        rows = _board(extra=extra, events={twin: "fnatic vs. NIP"})
        assert _short(_map(rows, "fnc-nip", 1, "NIP")) == (
            f"astatc-cs2-fnc-nip-{D}-map1", "yes", LONG, "premap_map_winner")

    def test_his_slug_league_code_must_be_the_identifiers(self, armed):
        rows = _board()
        h = _resolve(rows, f"val-fnc-nip-{D}-game1",
                     "Counter-Strike: fnatic vs NIP - Map 1 Winner", "NIP", ev=T_FNC_NIP)
        assert h is None


class TestTheMapPickPrefersHisOwnCodesWhenTheVenueCarriesThem:
    """A same-league rematch of the two teams on one day under the
    codes reversed (a bracket's rematch): his slug's codes are on the
    board byte for byte, so an identifier under other codes is not
    his event."""

    def test_a_rematch_under_other_codes_is_not_his_map(self, armed):
        re_ev = f"aec-cs2-nip-fnc-{D}"
        extra = [(re_ev, _yn(f"astatc-cs2-nip-fnc-{D}-map1", "Will NIP win Map 1 vs fnatic?"))]
        rows = _board(extra=extra, drop=(f"astatc-cs2-fnc-nip-{D}-map1",),
                      events={re_ev: "NIP vs. fnatic"})
        assert _map(rows, "fnc-nip", 1, "NIP") is None


class TestTheWordingArmNeverSeesTheMapRows:
    def test_match_side_is_not_called_for_a_map_slug(self, armed, monkeypatch):
        calls: list = []
        orig = premap.match_side

        def spy(rows, outcome, his_title, his_slug=None, **kw):
            calls.append(his_slug)
            return orig(rows, outcome, his_title, his_slug, **kw)

        monkeypatch.setattr(premap, "match_side", spy)
        assert _short(_map(_board(), "fnc-nip", 1, "NIP")) == (
            f"astatc-cs2-fnc-nip-{D}-map1", "yes", LONG, "premap_map_winner")
        _map_ex(_board(), "fnc-nip", 1, "NIP")
        assert calls == []

    def test_a_map_row_whose_side_names_his_team_is_never_a_wording_hit(self, armed):
        ident = f"astatc-cs2-fnc-nip-{D}-map1"
        poison = [(f"aec-cs2-fnc-nip-{D}", {"slug": ident, "question": "Will NIP win Map 1 vs fnatic?",
                   "marketSides": [{"identifier": ident, "description": "NIP", "long": True},
                                   {"identifier": ident, "description": "fnatic", "long": False}]})]
        rows = _board(extra=poison, drop=(ident,))
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:side"


class TestTheDateGuardHoldsWithoutTheKeys:
    def test_another_days_map_row_in_the_pool_is_refused(self, armed):
        class _Any(_Pool):
            async def fetch(self, sql, *a):
                return [dict(r) for r in self.rows]

        old_ev = "aec-cs2-fnc-nip-2026-09-05"
        old = [(old_ev, _yn("astatc-cs2-fnc-nip-2026-09-05-map1", "Will NIP win Map 1 vs fnatic?"))]
        rows = _board(extra=old, drop=(f"astatc-cs2-fnc-nip-{D}-map1",), events={old_ev: "fnatic vs. NIP"})
        h = asyncio.run(premap.resolve(_Any(rows), "Counter-Strike: fnatic vs NIP - Map 1 Winner",
                                       T_FNC_NIP, "NIP", f"cs2-fnc-nip-{D}-game1"))
        assert h is None


class TestTotalMapsNeverReachesTheSpreadLane:
    S = f"cs2-fokus-nemi1-{D}-tot-2pt5"

    @pytest.mark.parametrize("title,outcome", [
        ("FOKUS vs. Nemiga: O/U 2.5", "Over"), (None, "Over 2.5"), (None, "Under"),
        ("Spread: FOKUS (-2.5)", "FOKUS"), (None, "Nemiga"),
    ])
    def test_off(self, dark, title, outcome):
        from sportsassets.workers.premap import _want_prefixes
        assert _want_prefixes(self.S) is None
        assert _resolve(_board(), self.S, title, outcome, ev=None) is None
        assert _resolve(_board(), self.S, title, outcome,
                        ev="Counter-Strike: FOKUS vs Nemiga (BO3) - x") is None

    @pytest.mark.parametrize("title,outcome", [
        ("FOKUS vs. Nemiga: O/U 2.5", "Over"), (None, "Over 2.5"), (None, "Under"),
        ("Spread: FOKUS (-2.5)", "FOKUS"), (None, "Nemiga"),
    ])
    def test_on(self, armed, title, outcome):
        from sportsassets.workers.premap import _want_prefixes
        assert _want_prefixes(self.S) == {"tsc"}
        ev = "Counter-Strike: FOKUS vs Nemiga (BO3) - x"
        assert _resolve(_board(), self.S, title, outcome, ev=ev) is None
        ex = asyncio.run(premap.resolve_explain(_Pool(_board()), title, ev, outcome, self.S))
        assert ex["step"] == "unknown_market_type" and ex["split"] == "total:segment-absent"
