"""M4 (2026-09-07): E1, the esports event-title key, and E2, the map-winner
family -- his cs2 book against the venue's own rows.

The venue rows are esports_chi_rows_1352.txt Q4 (the aec-cs2 board:
identifier, sides, intents, the clock line, question, event title),
nf-venue_1350.log (the astatc map rows and the codes census) and
esports_chi_rows_1403.log lines 280-326 (the fokus-nemi / k27-sin map
rows, the tsc tot-2pt5 rows, the asc hcap rows), verbatim. His rows are
esports_chi_rows_1352.txt Q1 (slug, market title, event title, outcome),
verbatim. The k27-sin aec row's SIDES were not printed by any read (the
Q4 listing was cut at hero-1win); they are built here as every printed
aec-cs2 row builds them -- the side is the team name exactly as the
venue's event title 'K27 vs. Sinners' states it -- and said so in the
notes.

These pins sit beside test_c3_derivatives.py's key-builder and family
pins (a separate file so another builder's edit of event_keys_for merges).
Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import copy_sports as cs
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"
D7 = "2026-09-07"


# ----------------------------------------------------------- the venue

def _aec(ident: str, a: str, b: str, when: str) -> dict:
    """The aec-cs2 shape (Q4): both sides share the identifier and are
    described by the team NAME; the question carries the clock."""
    q = (f"Who will win in the upcoming esports event {a} vs {b} scheduled for "
         f"{when} UTC?")
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": a, "long": True},
        {"identifier": ident, "description": b, "long": False}]}


def _yn(ident: str, q: str, *, yes_long: bool = True) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": yes_long},
        {"identifier": ident, "description": "No", "long": not yes_long}]}


def _tsc(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Over", "long": True},
        {"identifier": ident, "description": "Under", "long": False}]}


def _asc(ident: str, q: str) -> dict:
    line = ident.rsplit("-", 1)[-1].replace("pt", ".")
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": line, "long": True},
        {"identifier": ident, "description": line, "long": False}]}


EVENTS = {
    f"aec-cs2-fokus-nemi-{D}": "FOKUS vs. Nemiga",
    f"aec-cs2-k27-sin-{D}": "K27 vs. Sinners",
    f"aec-cs2-bbc-sin-{D}": "BBL vs. Sinners",
    f"aec-cs2-fokus-astr-{D}": "FOKUS vs. ASTRAL",
    f"aec-cs2-1win-astr-{D}": "1WIN vs. ASTRAL",
    f"aec-cs2-1win-g1-{D}": "1WIN vs. GenOne",
    f"aec-cs2-erx-for-{D7}": "ex-RUSTEC vs. Fortress",
    f"aec-cs2-fnc-nip-{D}": "fnatic vs. NIP",
    f"aec-cs2-hero-1win-{D}": "Heroic vs. 1WIN",
}


def _venue() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    ev = f"aec-cs2-fokus-nemi-{D}"
    out += [(ev, _aec(ev, "FOKUS", "Nemiga", "September 6, 2026 at 11:00 AM")),
            (ev, _yn(f"astatc-cs2-fokus-nemi-{D}-map1", "Will FOKUS win Map 1 vs Nemiga?")),
            (ev, _yn(f"astatc-cs2-fokus-nemi-{D}-map2", "Will FOKUS win Map 2 vs Nemiga?")),
            (ev, _tsc(f"tsc-cs2-fokus-nemi-{D}-tot-2pt5",
                      "Will the total in FOKUS vs. Nemiga be more than 2.5?")),
            (ev, _asc(f"asc-cs2-fokus-nemi-{D}-hcap-neg-1pt5",
                      "Will the FOKUS cover -1.5 vs the Nemiga in FOKUS vs. Nemiga?")),
            (ev, _asc(f"asc-cs2-fokus-nemi-{D}-hcap-pos-1pt5",
                      "Will the FOKUS cover 1.5 vs the Nemiga in FOKUS vs. Nemiga?"))]
    ev = f"aec-cs2-k27-sin-{D}"
    out += [(ev, _aec(ev, "K27", "Sinners", "September 6, 2026 at 6:00 PM")),
            (ev, _yn(f"astatc-cs2-k27-sin-{D}-map2", "Will K27 win Map 2 vs Sinners?")),
            (ev, _tsc(f"tsc-cs2-k27-sin-{D}-tot-2pt5",
                      "Will the total in K27 vs. Sinners be more than 2.5?"))]
    ev = f"aec-cs2-bbc-sin-{D}"
    out += [(ev, _aec(ev, "BBL", "Sinners", "September 6, 2026 at 4:00 PM"))]
    ev = f"aec-cs2-fokus-astr-{D}"
    out += [(ev, _aec(ev, "FOKUS", "ASTRAL", "September 6, 2026 at 2:00 PM"))]
    ev = f"aec-cs2-1win-astr-{D}"
    out += [(ev, _aec(ev, "1WIN", "ASTRAL", "September 6, 2026 at 3:30 PM")),
            (ev, _yn(f"astatc-cs2-1win-astr-{D}-map2", "Will 1WIN win Map 2 vs ASTRAL?")),
            (ev, _tsc(f"tsc-cs2-1win-astr-{D}-tot-2pt5",
                      "Will the total in 1WIN vs. ASTRAL be more than 2.5?")),
            (ev, _asc(f"asc-cs2-1win-astr-{D}-hcap-neg-1pt5",
                      "Will the 1WIN cover -1.5 vs the ASTRAL in 1WIN vs. ASTRAL?"))]
    ev = f"aec-cs2-1win-g1-{D}"
    out += [(ev, _aec(ev, "1WIN", "GenOne", "September 6, 2026 at 2:00 PM"))]
    ev = f"aec-cs2-erx-for-{D7}"
    out += [(ev, _aec(ev, "ex-RUSTEC", "Fortress", "September 7, 2026 at 8:00 AM"))]
    ev = f"aec-cs2-fnc-nip-{D}"
    out += [(ev, _aec(ev, "fnatic", "NIP", "September 6, 2026 at 10:00 AM")),
            (ev, _yn(f"astatc-cs2-fnc-nip-{D}-map1", "Will NIP win Map 1 vs fnatic?")),
            (ev, _yn(f"astatc-cs2-fnc-nip-{D}-map2", "Will NIP win Map 2 vs fnatic?")),
            (ev, _tsc(f"tsc-cs2-fnc-nip-{D}-tot-2pt5",
                      "Will the total in NIP vs. fnatic be more than 2.5?"))]
    ev = f"aec-cs2-hero-1win-{D}"
    out += [(ev, _aec(ev, "Heroic", "1WIN", "September 6, 2026 at 6:00 PM")),
            (ev, _yn(f"astatc-cs2-hero-1win-{D}-map2", "Will Heroic win Map 2 vs 1WIN?")),
            (ev, _tsc(f"tsc-cs2-hero-1win-{D}-tot-2pt5",
                      "Will the total in Heroic vs. 1WIN be more than 2.5?")),
            (ev, _asc(f"asc-cs2-hero-1win-{D}-hcap-neg-1pt5",
                      "Will the Heroic cover -1.5 vs the 1WIN in Heroic vs. 1WIN?"))]
    return out


def _board(extra=(), drop=(), events=None) -> list[dict]:
    rows: list[dict] = []
    titles = dict(EVENTS, **(events or {}))
    kept = [(ev, m) for ev, m in _venue() if m["slug"] not in drop]
    for ev, m in kept + list(extra):
        keys = premap.event_keys_for(titles[ev], ev)
        for r in premap._market_rows({"slug": ev, "title": titles[ev]}, m):
            r["event_keys"] = keys
            rows.append(r)
    return rows


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        if "us_premap" not in sql:
            return []
        k = set(a[0])
        return [dict(r) for r in self.rows if set(r["event_keys"]) & k]

    async def fetchval(self, sql, *a):
        return None


def _resolve(rows, slug, title, outcome, ev=None):
    return asyncio.run(premap.resolve(_Pool(rows), title, ev, outcome, slug))


def _explain(rows, slug, title, outcome, ev=None):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, outcome, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


# his event titles (Q1, verbatim)
T_FOKUS_NEMI = "Counter-Strike: FOKUS vs Nemiga (BO3) - Stake Ranked Episode 4: Closed Qualifier Playoffs"
T_K27_SIN = "Counter-Strike: K27 vs Sinners (BO3) - PGL Masters Bucharest: European Open Qualifier #2 Playoffs"
T_BBL_SIN = "Counter-Strike: BBL vs Sinners (BO3) - PGL Masters Bucharest: European Open Qualifier #2 Playoffs"
T_FOKUS_AST = "Counter-Strike: FOKUS vs ASTRAL (BO1) - PGL Masters European Open Qualifier #2 Playoffs"
T_1WIN_AST = "Counter-Strike: 1WIN vs ASTRAL (BO3) - PGL Masters Bucharest: European Open Qualifier #2 Playoffs"
T_RUSTEC_FOR = "Counter-Strike: ex-RUSTEC vs Fortress (BO3) - NODWIN Clutch Series Play-In Group B"
T_FNC_NIP = "Counter-Strike: fnatic vs NIP (BO3) - Stake Ranked Episode 4: Closed Qualifier Playoffs"
T_HERO_1WIN = "Counter-Strike: Heroic vs 1WIN (BO3) - PGL Masters Bucharest: European Open Qualifier #2 Playoffs"
T_SIN_CS1 = "Counter-Strike: Sinners vs CYBERSHOKE Esports (BO1) - PGL Masters European Open Qualifier #2 Playoffs"


# --------------------------------------------- 1. E1, the key builder

class TestTheEsportsEventTitleKey:
    def test_the_attested_wording_keys_its_matchup_both_ways(self):
        keys = premap.event_keys_for(T_FOKUS_NEMI, f"cs2-fokus-nemi1-{D}")
        assert f"fokus vs nemiga@{D}" in keys and f"nemiga vs fokus@{D}" in keys
        assert f"cs2-fokus-nemi1-{D}" in keys
        assert not any("stake" in k or "episode" in k or "bo3" in k for k in keys)
        # the venue's own event title builds the same keys
        venue = set(premap.event_keys_for("FOKUS vs. Nemiga", f"aec-cs2-fokus-nemi-{D}"))
        assert {f"fokus vs nemiga@{D}", f"nemiga vs fokus@{D}"} <= venue
        assert premap._esports_matchup(T_RUSTEC_FOR) == "ex-RUSTEC vs Fortress"
        assert f"ex rustec vs fortress@{D7}" in premap.event_keys_for(T_RUSTEC_FOR, f"cs2-rustec-for10-{D7}")
        assert f"1win vs astral@{D}" in premap.event_keys_for(T_1WIN_AST, f"cs2-1win-ast-{D}-game2")

    def test_a_title_outside_the_template_keys_as_today(self):
        for t in ("Counter-Strike: fnatic vs NIP - Map 1 Winner",
                  "Map Handicap: NIP (-1.5) vs fnatic (+1.5)",
                  "Counter-Strike: fnatic vs NIP (BO3)- Stake",
                  "Dota 2: fnatic vs NIP (BO3) - x",
                  "Will fnatic vs. NIP end in a draw?"):
            assert premap._esports_matchup(t) is None, t
        keys = premap.event_keys_for("Counter-Strike: fnatic vs NIP - Map 1 Winner", f"cs2-fnc-nip-{D}-game1")
        assert f"fnatic vs nip@{D}" not in keys and f"cs2-fnc-nip-{D}" in keys
        # the C3 draw template is untouched
        assert f"olympique de marseille vs paris fc@{D}" in premap.event_keys_for(
            "Will Olympique de Marseille vs. Paris FC end in a draw?", f"fl1-olm-pfc-{D}-draw")

    def test_the_lookup_is_not_a_decision(self):
        src = inspect.getsource(premap._esports_matchup) + inspect.getsource(premap.event_keys_for)
        for forbidden in ("match_side", "intent", "side_norm", "SequenceMatcher"):
            assert forbidden not in src


# --------------------------------- 2. E1, the series maps by the side name

class TestTheSeriesMapsByTheVenuesOwnSideName:
    @pytest.mark.parametrize("slug,title,outcome,want_id,side,intent", [
        (f"cs2-fokus-nemi1-{D}", T_FOKUS_NEMI, "Nemiga", f"aec-cs2-fokus-nemi-{D}", "nemiga", SHORT),
        (f"cs2-fokus-nemi1-{D}", T_FOKUS_NEMI, "FOKUS", f"aec-cs2-fokus-nemi-{D}", "fokus", LONG),
        (f"cs2-k271-sin2-{D}", T_K27_SIN, "K27", f"aec-cs2-k27-sin-{D}", "k27", LONG),
        (f"cs2-rustec-for10-{D7}", T_RUSTEC_FOR, "ex-RUSTEC", f"aec-cs2-erx-for-{D7}", "ex rustec", LONG),
        (f"cs2-fokus-ast-{D}", T_FOKUS_AST, "ASTRAL", f"aec-cs2-fokus-astr-{D}", "astral", SHORT),
        (f"cs2-bbl1-sin2-{D}", T_BBL_SIN, "Sinners", f"aec-cs2-bbc-sin-{D}", "sinners", SHORT),
        (f"cs2-fnc-nip-{D}", T_FNC_NIP, "NIP", f"aec-cs2-fnc-nip-{D}", "nip", SHORT),
    ])
    def test_the_pins(self, monkeypatch, slug, title, outcome, want_id, side, intent):
        # a series' market title IS his event title; the decision is the
        # wording arm's named branch, with or without the switch
        for env in ("on", ""):
            monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, env)
            h = _resolve(_board(), slug, title, outcome, ev=title)
            assert _short(h) == (want_id, side, intent, "premap"), (env, slug, outcome)
            assert _explain(_board(), slug, title, outcome, ev=title)["step"] == "resolves"

    def test_a_side_that_is_not_his_outcome_verbatim_refuses(self, armed):
        rows = _board()
        assert _resolve(rows, f"cs2-fokus-nemi1-{D}", T_FOKUS_NEMI, "Nemiga Gaming", ev=T_FOKUS_NEMI) is None
        ex = _explain(rows, f"cs2-fokus-nemi1-{D}", T_FOKUS_NEMI, "Nemiga Gaming", ev=T_FOKUS_NEMI)
        assert ex["step"] == "no_side_match"
        # and a name from another series on the same day is never a candidate
        assert _resolve(rows, f"cs2-fokus-nemi1-{D}", T_FOKUS_NEMI, "1WIN", ev=T_FOKUS_NEMI) is None

    def test_a_series_the_venue_has_not_listed_stays_unmapped(self, armed):
        keys = premap.event_keys_for(T_SIN_CS1, f"cs2-sin2-cs1-{D}")
        assert f"sinners vs cybershoke esports@{D}" in keys
        assert _resolve(_board(), f"cs2-sin2-cs1-{D}", T_SIN_CS1, "Sinners", ev=T_SIN_CS1) is None
        assert _explain(_board(), f"cs2-sin2-cs1-{D}", T_SIN_CS1, "Sinners", ev=T_SIN_CS1)["step"] == "no_key_intersection"

    def test_the_same_team_twice_on_one_day_meets_only_its_own_matchup(self, armed):
        # 1WIN played GenOne and ASTRAL on the 6th: his ASTRAL series keys
        # reach the astr event alone (the genone sides are never printed
        # as candidates). The series itself still REFUSES today, and not
        # for E1's reason: the wording arm's line reader takes the '1' of
        # '1WIN' right after 'Counter-Strike:' as a line (_LINE_CTX's ':'
        # context -> his_lines ['1']) and the unlined aec row fails the
        # line guard. That is the decision arm, not the lookup, so it is
        # pinned as it stands and named in the notes -- not widened here.
        # (His book carried only this series' -game2, which maps: the
        # map pick reads no line.)
        assert _resolve(_board(), f"cs2-1win-ast-{D}", T_1WIN_AST, "1WIN", ev=T_1WIN_AST) is None
        ex = _explain(_board(), f"cs2-1win-ast-{D}", T_1WIN_AST, "1WIN", ev=T_1WIN_AST)
        assert ex["step"] == "no_side_match" and "his_lines=['1']" in ex["detail"]
        assert "genone" not in ex["detail"] and "('1win', '30')" in ex["detail"]


# --------------------------------------------- 3. E2, the readers

class TestTheMapWinnerReaders:
    def test_family_and_segment(self):
        s = f"cs2-fnc-nip-{D}-game1"
        assert cs.family_of(s) == "map_winner" and cs.segment_of(s) == "map1"
        assert cs.family_of(f"cs2-k271-sin2-{D}-game2") == "map_winner"
        assert cs.segment_of(f"cs2-k271-sin2-{D}-game2") == "map2"
        assert cs.market_type_of(s) == "unknown"                # the copy lane: as today
        assert cs.mirror_family_of(s) == "map_winner" and ms._family_of(s) == "map_winner"
        assert "map_winner" in rules.MIRROR_FAMILIES
        assert premap.map_his(s) == {"lg": "cs2", "a": "fnc", "b": "nip", "date": D, "n": 1}
        assert premap.c3_his(s) is None
        # not a map winner: a segment before it, a second token, a bare word
        for bad in (f"cs2-fnc-nip-{D}-fh-game1", f"cs2-fnc-nip-{D}-game1-winner",
                    f"cs2-fnc-nip-{D}-game", f"cs2-fnc-nip-{D}-map1", f"cs2-nip-nip-{D}-game1"):
            assert cs.family_of(bad) != "map_winner" or premap.map_his(bad) is None, bad

    def test_total_maps_is_the_total_family_on_the_maps_segment(self):
        s = f"cs2-fokus-nemi1-{D}-tot-2pt5"
        assert cs.family_of(s) == "total" and cs.segment_of(s) == "maps"
        assert cs.mirror_family_of(s) == "total"
        # prop for the copy lane, as a first-half total is: no venue
        # prefix, so the switch-off path refuses instead of routing
        assert cs.market_type_of(s) == "prop"
        assert premap.c3_his(s) == {"family": "total", "seg": "maps", "lg": "cs2", "a": "fokus",
                                    "b": "nemi1", "date": D, "line": "2.5", "side": ""}
        # a 'tot' that is one of his slug's own team codes is that code
        assert cs.market_type_of(f"epl-tot-ars-{D}-tot") == "moneyline"
        assert cs.market_type_of(f"epl-tot-ars-{D}-tot-1pt5") == "spread"
        assert cs.family_of(f"epl-tot-ars-{D}-tot-1pt5") == "spread"
        assert cs.segment_of(f"epl-tot-ars-{D}-tot-1pt5") == ""


# --------------------------------------------- 4. E2, the map winner

MAP = {
    "fnc-nip": ("Counter-Strike: fnatic vs NIP - Map {n} Winner", T_FNC_NIP),
    "hero-1win": ("Counter-Strike: Heroic vs 1WIN - Map {n} Winner", T_HERO_1WIN),
    "1win-ast": ("Counter-Strike: 1WIN vs ASTRAL - Map {n} Winner", T_1WIN_AST),
    "fokus-nemi1": ("Counter-Strike: FOKUS vs Nemiga - Map {n} Winner", T_FOKUS_NEMI),
    "k271-sin2": ("Counter-Strike: K27 vs Sinners - Map {n} Winner", T_K27_SIN),
}


def _map(rows, codes, n, outcome, date=D, title=None, ev="-"):
    t, e = MAP[codes]
    return _resolve(rows, f"cs2-{codes}-{date}-game{n}",
                    t.format(n=n) if title is None else title, outcome,
                    ev=e if ev == "-" else ev)


def _map_ex(rows, codes, n, outcome, date=D, title=None, ev="-"):
    t, e = MAP[codes]
    return _explain(rows, f"cs2-{codes}-{date}-game{n}",
                    t.format(n=n) if title is None else title, outcome,
                    ev=e if ev == "-" else ev)


class TestTheMapWinnerMapsTheSubjectOnly:
    @pytest.mark.parametrize("codes,n,outcome,want_id", [
        ("fnc-nip", 1, "NIP", f"astatc-cs2-fnc-nip-{D}-map1"),
        ("fnc-nip", 2, "NIP", f"astatc-cs2-fnc-nip-{D}-map2"),
        ("hero-1win", 2, "Heroic", f"astatc-cs2-hero-1win-{D}-map2"),
        ("1win-ast", 2, "1WIN", f"astatc-cs2-1win-astr-{D}-map2"),
        ("fokus-nemi1", 1, "FOKUS", f"astatc-cs2-fokus-nemi-{D}-map1"),
        ("fokus-nemi1", 2, "FOKUS", f"astatc-cs2-fokus-nemi-{D}-map2"),
        ("k271-sin2", 2, "K27", f"astatc-cs2-k27-sin-{D}-map2"),
    ])
    def test_his_buy_of_the_subject_is_the_yes_row(self, armed, codes, n, outcome, want_id):
        h = _map(_board(), codes, n, outcome)
        assert _short(h) == (want_id, "yes", LONG, "premap_map_winner")
        ex = _map_ex(_board(), codes, n, outcome)
        assert ex["step"] == "resolves" and ex["matched_by"] == "premap_map_winner"
        assert ex["family"] == "map_winner" and ex["map"]["admitted"] == want_id
        assert ex["map"]["side"] == "yes"

    @pytest.mark.parametrize("codes,n,outcome", [
        ("fnc-nip", 1, "fnatic"), ("fnc-nip", 2, "fnatic"), ("hero-1win", 2, "1WIN"),
        ("1win-ast", 2, "ASTRAL"), ("fokus-nemi1", 1, "Nemiga"), ("k271-sin2", 2, "Sinners"),
    ])
    def test_his_fill_on_the_other_team_refuses_the_complement_is_not_assumed(self, armed, codes, n, outcome):
        rows = _board()
        assert _map(rows, codes, n, outcome) is None
        ex = _map_ex(rows, codes, n, outcome)
        assert ex["step"] == "no_side_match" and ex["split"] == "map:subject-absent"
        assert ex["map"]["refusal"] == "map:subject-absent"

    def test_a_map_the_venue_does_not_list_is_segment_absent(self, armed):
        rows = _board()
        assert _map(rows, "hero-1win", 1, "Heroic") is None
        ex = _map_ex(rows, "hero-1win", 1, "Heroic")
        assert ex["split"] == "map:segment-absent"
        assert _map_ex(rows, "fnc-nip", 3, "NIP")["split"] == "map:segment-absent"
        # the astatc rows of another map are never the candidate
        assert _map(rows, "1win-ast", 1, "1WIN") is None

    def test_the_digit_suffixed_codes_reach_the_rows_only_through_his_event_title(self, armed):
        rows = _board()
        assert _map_ex(rows, "1win-ast", 2, "1WIN", ev=None)["step"] == "no_key_intersection"
        assert _map(rows, "1win-ast", 2, "1WIN", ev=None) is None
        assert _map_ex(rows, "fokus-nemi1", 1, "FOKUS", ev=None)["step"] == "no_key_intersection"
        # equal codes reach them by the slug key alone
        assert _short(_map(rows, "fnc-nip", 1, "NIP", ev=None)) == (
            f"astatc-cs2-fnc-nip-{D}-map1", "yes", LONG, "premap_map_winner")

    def test_his_title_must_name_his_slugs_map(self, armed):
        rows = _board()
        for t in ("Counter-Strike: fnatic vs NIP - Map 2 Winner",
                  "Counter-Strike: fnatic vs NIP - Map Winner",
                  "Counter-Strike: fnatic vs NIP (BO3) - Stake Ranked Episode 4: Closed Qualifier Playoffs",
                  "Map 1 Winner: fnatic vs NIP", ""):
            assert _map(rows, "fnc-nip", 1, "NIP", title=t) is None, t
            assert _map_ex(rows, "fnc-nip", 1, "NIP", title=t)["split"] == "map:title-shear", t

    def test_the_question_must_be_the_attested_template_with_his_map(self, armed):
        odd = [(f"aec-cs2-fnc-nip-{D}", _yn(f"astatc-cs2-fnc-nip-{D}-map1", "Will NIP win the first map?"))]
        rows = _board(extra=odd, drop=(f"astatc-cs2-fnc-nip-{D}-map1",))
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:question-shape"
        odd = [(f"aec-cs2-fnc-nip-{D}", _yn(f"astatc-cs2-fnc-nip-{D}-map1", "Will NIP win Map 2 vs fnatic?"))]
        rows = _board(extra=odd, drop=(f"astatc-cs2-fnc-nip-{D}-map1",))
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:question-shape"

    def test_the_question_must_name_his_two_teams(self, armed):
        odd = [(f"aec-cs2-fnc-nip-{D}", _yn(f"astatc-cs2-fnc-nip-{D}-map1", "Will NIP win Map 1 vs Vitality?"))]
        rows = _board(extra=odd, drop=(f"astatc-cs2-fnc-nip-{D}-map1",))
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:names"
        # his outcome must be one side of his own title's matchup
        rows = _board()
        for o in ("Yes", "No", "Vitality", "NIP Gaming"):
            assert _map(rows, "fnc-nip", 1, o) is None, o
            assert _map_ex(rows, "fnc-nip", 1, o)["split"] == "map:names", o

    def test_two_yes_rows_on_his_identifier_are_ambiguous(self, armed):
        # M4 review: a twin under another game code (cs2x) is not an
        # ambiguity but another event, refused before the names are read
        # (test_m4_review_pins); the ambiguity that remains is on HIS
        # identifier itself -- the sweep listing two yes sides on one row
        ident = f"astatc-cs2-fnc-nip-{D}-map1"
        extra = [(f"aec-cs2-fnc-nip-{D}", _yn(ident, "Will NIP win Map 1 vs fnatic?"))]
        rows = _board(extra=extra)
        assert sum(1 for r in rows if r["identifier"] == ident and r["side_norm"] == "yes") == 2
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:ambiguous"

    def test_a_twin_under_another_game_code_is_another_event(self, armed):
        twin = f"aec-cs2x-fnc-nip-{D}"
        extra = [(twin, _yn(f"astatc-cs2x-fnc-nip-{D}-map1", "Will NIP win Map 1 vs fnatic?"))]
        # beside his cs2 row: his row alone; without it: no candidate at all
        rows = _board(extra=extra, events={twin: "fnatic vs. NIP"})
        assert _short(_map(rows, "fnc-nip", 1, "NIP")) == (
            f"astatc-cs2-fnc-nip-{D}-map1", "yes", LONG, "premap_map_winner")
        rows = _board(extra=extra, drop=(f"astatc-cs2-fnc-nip-{D}-map1",), events={twin: "fnatic vs. NIP"})
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:segment-absent"

    def test_a_same_league_rematch_under_reversed_codes_is_another_event(self, armed):
        re_ev = f"aec-cs2-nip-fnc-{D}"
        extra = [(re_ev, _yn(f"astatc-cs2-nip-fnc-{D}-map1", "Will NIP win Map 1 vs fnatic?"))]
        rows = _board(extra=extra, drop=(f"astatc-cs2-fnc-nip-{D}-map1",), events={re_ev: "NIP vs. fnatic"})
        # his own stem (fnc-nip map2) is on the board: the reversed codes are not his event
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:segment-absent"
        # the digit-suffixed case carries no stem on the board and still maps
        assert _short(_map(rows, "1win-ast", 2, "1WIN")) == (
            f"astatc-cs2-1win-astr-{D}-map2", "yes", LONG, "premap_map_winner")

    def test_the_intent_is_the_venues_own(self, armed):
        odd = [(f"aec-cs2-fnc-nip-{D}", _yn(f"astatc-cs2-fnc-nip-{D}-map1", "Will NIP win Map 1 vs fnatic?",
                                            yes_long=False))]
        rows = _board(extra=odd, drop=(f"astatc-cs2-fnc-nip-{D}-map1",))
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        assert _map_ex(rows, "fnc-nip", 1, "NIP")["split"] == "map:intent"

    def test_never_the_no_row_and_never_the_series_row(self, armed):
        rows = _board()
        for codes, n, o in (("fnc-nip", 1, "NIP"), ("hero-1win", 2, "Heroic"), ("k271-sin2", 2, "K27")):
            h = _map(rows, codes, n, o)
            assert h["outcome"] == "yes" and h["intent"] == LONG
            assert h["market_slug"].startswith("astatc-")
        # the map slug never lands on the aec series row, the tot row or the hcap row
        assert _map(rows, "fnc-nip", 1, "fnatic") is None
        assert _map(rows, "fokus-nemi1", 1, "Nemiga") is None

    def test_the_census_prints_the_family_refusal(self, armed):
        p = _Pool(_board())
        t, e = MAP["fnc-nip"]
        ctx = {"title": t.format(n=1), "event_title": e, "outcome": "fnatic",
               "his_slug": f"cs2-fnc-nip-{D}-game1"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "no_side_match:map:subject-absent"
        ctx["outcome"] = "NIP"
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "resolves"
        t, e = MAP["hero-1win"]
        ctx = {"title": t.format(n=1), "event_title": e, "outcome": "Heroic",
               "his_slug": f"cs2-hero-1win-{D}-game1"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "no_side_match:map:segment-absent"


# ------------------------------------------ 5. total maps, the switch

class TestTotalMapsRefusesAtANamedTotalStep:
    S = f"cs2-fokus-nemi1-{D}-tot-2pt5"

    def test_on_it_is_the_segment_gate(self, armed):
        rows = _board()
        assert _resolve(rows, self.S, None, "Over", ev=T_FOKUS_NEMI) is None
        assert _resolve(rows, self.S, None, "Under", ev=T_FOKUS_NEMI) is None
        ex = _explain(rows, self.S, None, "Over", ev=T_FOKUS_NEMI)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "total:segment-absent"
        assert ex["refusal"] == "total:segment-absent" and ex["family"] == "total"
        # the venue's tsc-…-tot-2pt5 row was on the board and was never a candidate
        assert any(r["identifier"] == f"tsc-cs2-fokus-nemi-{D}-tot-2pt5" for r in rows)

    def test_off_it_is_the_prop_family_gap_never_the_spread_lane(self, dark):
        rows = _board()
        assert _resolve(rows, self.S, None, "Over", ev=T_FOKUS_NEMI) is None
        ex = _explain(rows, self.S, None, "Over", ev=T_FOKUS_NEMI)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "family_not_listed"


class TestTheSwitchHoldsTheMapWinner:
    def test_off_the_map_winner_is_unparsed_as_today(self, dark):
        rows = _board()
        assert _map(rows, "fnc-nip", 1, "NIP") is None
        ex = _map_ex(rows, "fnc-nip", 1, "NIP")
        assert ex["step"] == "unknown_market_type" and ex["split"] == "unparsed"
        assert premap._want_prefixes(f"cs2-fnc-nip-{D}-game1") is None

    def test_on_the_kind_is_the_map_rows(self, armed):
        assert premap._want_prefixes(f"cs2-fnc-nip-{D}-game1") == {"astatc"}
        assert premap._want_prefixes(f"cs2-fokus-nemi1-{D}-tot-2pt5") == {"tsc"}

    def test_the_pick_is_pure_and_the_lane_sits_inside_the_switch(self):
        for fn in (premap._c3_pick_map, premap.map_his, premap._esports_matchup):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "SequenceMatcher"):
                assert forbidden not in src, (fn.__name__, forbidden)
        src = inspect.getsource(premap.resolve)
        assert "his_map = map_his(global_slug) if yn_identity_on()" in src
        assert "_c3_pick_map(kept, his_map, outcome, market_title, {})" in src
