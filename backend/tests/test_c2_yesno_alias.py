"""C2 (2026-09-06, owner: "I need you to map more of his trades ... la
liga should be very easy to map"; "we need more volume"): the per-team
yes/no identity branch maps his soccer.

The venue's own rows for five of his 2026-09-06 events (us_premap, read
19:09Z, preset premap-rows) are reproduced below verbatim -- identifier,
side, intent and question -- and the seven feed rows are run against
them exactly as production would with PREMAP_YN_IDENTITY=on. Four gates
refused every one of them before this patch; each is pinned here in
both directions: what maps now, and what still refuses, by name.

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import pmus
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"

# ---------------------------------------------------------------------------
# the venue's rows, verbatim from the 19:09Z dump (per-team + draw rows;
# the exact-score / halftime / second-half rows of the same events are
# reproduced for the Serie A derby only -- they never carry a per-team
# suffix and the branch never looks at them)

VENUE = {
    # event slug (venue) -> (event title, [(identifier, question), ...])
    "aec-sea-juv-mil-2026-09-06": ("Juventus FC vs. AC Milan", [
        ("atc-sea-juv-mil-2026-09-06-juv",
         "Will Juventus FC win against AC Milan in the Serie A match scheduled for Sep 6, 2026?"),
        ("atc-sea-juv-mil-2026-09-06-mil",
         "Will AC Milan win against Juventus FC in the Serie A match scheduled for Sep 6, 2026?"),
        ("atc-sea-juv-mil-2026-09-06-draw",
         "Will the Serie A match Juventus FC vs AC Milan scheduled for Sep 6, 2026 end in a draw?"),
        ("atc-sea-juv-mil-2026-09-06-fh-juv", "Will Juventus FC lead AC Milan at halftime?"),
        ("atc-sea-juv-mil-2026-09-06-sh-juv",
         "Will Juventus FC win the second half against AC Milan?"),
        ("atc-sea-juv-mil-2026-09-06-exact-score-1-0", "Will JUV vs MIL finish JUV wins 1-0?"),
    ]),
    "aec-sea-bol-sas-2026-09-06": ("Bologna FC 1909 vs. US Sassuolo Calcio", [
        ("atc-sea-bol-sas-2026-09-06-bol",
         "Will Bologna FC 1909 win against US Sassuolo Calcio in the Serie A match scheduled for "
         "Sep 6, 2026?"),
        ("atc-sea-bol-sas-2026-09-06-sas",
         "Will US Sassuolo Calcio win against Bologna FC 1909 in the Serie A match scheduled for "
         "Sep 6, 2026?"),
        ("atc-sea-bol-sas-2026-09-06-draw",
         "Will the Serie A match Bologna FC 1909 vs US Sassuolo Calcio scheduled for Sep 6, 2026 "
         "end in a draw?"),
    ]),
    "aec-lal-esp-sev-2026-09-06": ("RCD Espanyol de Barcelona vs. Sevilla FC", [
        ("atc-lal-esp-sev-2026-09-06-esp",
         "Will RCD Espanyol de Barcelona win against Sevilla FC in the La Liga match scheduled for "
         "Sep 6, 2026?"),
        ("atc-lal-esp-sev-2026-09-06-sev",
         "Will Sevilla FC win against RCD Espanyol de Barcelona in the La Liga match scheduled for "
         "Sep 6, 2026?"),
        ("atc-lal-esp-sev-2026-09-06-draw",
         "Will the La Liga match RCD Espanyol de Barcelona vs Sevilla FC scheduled for Sep 6, 2026 "
         "end in a draw?"),
    ]),
    "aec-els-kbk-tro-2026-09-06": ("Kristiansund BK vs. Tromso IL", [
        ("atc-els-kbk-tro-2026-09-06-kbk",
         "Will Kristiansund BK win against Tromso IL in the Eliteserien match scheduled for "
         "Sep 6, 2026?"),
        ("atc-els-kbk-tro-2026-09-06-tro",
         "Will Tromso IL win against Kristiansund BK in the Eliteserien match scheduled for "
         "Sep 6, 2026?"),
        ("atc-els-kbk-tro-2026-09-06-draw",
         "Will the Eliteserien match Kristiansund BK vs Tromso IL scheduled for Sep 6, 2026 end in "
         "a draw?"),
    ]),
    "aec-lpa-ros-new-2026-09-06": ("CA Rosario Central vs. CA Newell's Old Boys", [
        ("atc-lpa-ros-new-2026-09-06-ros",
         "Will CA Rosario Central win against CA Newell's Old Boys in the Liga Argentina match "
         "scheduled for Sep 6, 2026?"),
        ("atc-lpa-ros-new-2026-09-06-new",
         "Will CA Newell's Old Boys win against CA Rosario Central in the Liga Argentina match "
         "scheduled for Sep 6, 2026?"),
        ("atc-lpa-ros-new-2026-09-06-draw",
         "Will the Liga Argentina match CA Rosario Central vs CA Newell's Old Boys scheduled for "
         "Sep 6, 2026 end in a draw?"),
    ]),
}

# his feed, verbatim (trades.market_slug / market_title / markets.event_title / outcome)
FEED = [
    ("sea-juv-mil-2026-09-06-juv", "Will Juventus FC win on 2026-09-06?",
     "Juventus FC vs. AC Milan", "Yes"),
    ("sea-bol-sas-2026-09-06-sas", "Will US Sassuolo Calcio win on 2026-09-06?",
     "Bologna FC 1909 vs. US Sassuolo Calcio", "Yes"),
    ("lal-esp-sev-2026-09-06-esp", "Will RCD Espanyol de Barcelona win on 2026-09-06?",
     "RCD Espanyol de Barcelona vs. Sevilla FC", "No"),
    ("nor-kbk-tro-2026-09-06-tro", "Will Tromsø IL win on 2026-09-06?",
     "Kristiansund BK vs. Tromsø IL", "Yes"),
    ("arg-ros-new-2026-09-06-new", "Will CA Newell's Old Boys win on 2026-09-06?",
     "CA Rosario Central vs. CA Newell's Old Boys", "Yes"),
    ("fl1-olm-pfc-2026-09-06-olm", "Will Olympique de Marseille win on 2026-09-06?",
     "Olympique de Marseille vs. Paris FC", "Yes"),
    ("por-gui-cas-2026-09-06-gui", "Will Vitória SC win on 2026-09-06?",
     "Vitória SC vs. Casa Pia AC", "Yes"),
]

# the rows the dump does not carry (the query named five events): the
# venue's own wording under the league codes it listed tonight (lg1 x3
# games, lpb x3 games in the same read), so the alias rule is pinned on
# them too -- as an EXPECTATION, not an observation
VENUE_EXTRA = {
    "aec-lg1-olm-pfc-2026-09-06": ("Olympique de Marseille vs. Paris FC", [
        ("atc-lg1-olm-pfc-2026-09-06-olm",
         "Will Olympique de Marseille win against Paris FC in the Ligue 1 match scheduled for "
         "Sep 6, 2026?"),
        ("atc-lg1-olm-pfc-2026-09-06-pfc",
         "Will Paris FC win against Olympique de Marseille in the Ligue 1 match scheduled for "
         "Sep 6, 2026?"),
    ]),
    "aec-lpb-gui-cas-2026-09-06": ("Vitoria SC vs. Casa Pia AC", [
        ("atc-lpb-gui-cas-2026-09-06-gui",
         "Will Vitoria SC win against Casa Pia AC in the Primeira Liga match scheduled for "
         "Sep 6, 2026?"),
        ("atc-lpb-gui-cas-2026-09-06-cas",
         "Will Casa Pia AC win against Vitoria SC in the Primeira Liga match scheduled for "
         "Sep 6, 2026?"),
    ]),
}


def _market(ident: str, q: str) -> dict:
    """The venue's own side expansion: both sides share the identifier;
    `long` names the intent (probe NAMEDML-Q, the dump's intents)."""
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _board(venue: dict | None = None, extra: dict | None = None) -> list[dict]:
    """Rows built by the sweep's own row builder and keyed the way the
    sweep keys them (event_keys_for on the venue's event title + slug)."""
    rows: list[dict] = []
    for src in (venue if venue is not None else VENUE, extra or {}):
        for ev_slug, (ev_title, mkts) in src.items():
            keys = premap.event_keys_for(ev_title, ev_slug)
            for ident, q in mkts:
                for r in premap._market_rows({"slug": ev_slug, "title": ev_title},
                                             _market(ident, q)):
                    r["event_keys"] = keys
                    rows.append(r)
    return rows


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        assert "us_premap" in sql
        k = set(a[0])
        return [dict(r) for r in self.rows if set(r["event_keys"]) & k]


def _resolve(rows, slug, title, ev, outcome):
    return asyncio.run(premap.resolve(_Pool(rows), title, ev, outcome, slug))


def _explain(rows, slug, title, ev, outcome):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, outcome, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["intent"], h["matched_by"], h.get("league_alias"))


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _with_question(rows, ident, q):
    """The same board with one identifier's question rewritten."""
    return [dict(r, question=q) if r["identifier"] == ident else dict(r) for r in rows]


# ------------------------------------------------------------ 0. the table

EXPECT = {
    "sea-juv-mil-2026-09-06-juv": ("atc-sea-juv-mil-2026-09-06-juv", LONG, "premap_identity", None),
    "sea-bol-sas-2026-09-06-sas": ("atc-sea-bol-sas-2026-09-06-sas", LONG, "premap_identity", None),
    "lal-esp-sev-2026-09-06-esp": ("atc-lal-esp-sev-2026-09-06-esp", SHORT, "premap_identity", None),
    "nor-kbk-tro-2026-09-06-tro": ("atc-els-kbk-tro-2026-09-06-tro", LONG, "premap_alias", "nor->els"),
    "arg-ros-new-2026-09-06-new": ("atc-lpa-ros-new-2026-09-06-new", LONG, "premap_alias", "arg->lpa"),
    "fl1-olm-pfc-2026-09-06-olm": None,       # rows not in the dump
    "por-gui-cas-2026-09-06-gui": None,       # rows not in the dump
}
EXPECT_EXTRA = {
    "fl1-olm-pfc-2026-09-06-olm": ("atc-lg1-olm-pfc-2026-09-06-olm", LONG, "premap_alias", "fl1->lg1"),
    "por-gui-cas-2026-09-06-gui": ("atc-lpb-gui-cas-2026-09-06-gui", LONG, "premap_alias", "por->lpb"),
}


class TestTheSevenFeedRowsAgainstTheDump:
    @pytest.mark.parametrize("slug,title,ev,oc", FEED)
    def test_each_row_maps_or_refuses_exactly_as_the_table_says(self, armed, slug, title, ev, oc):
        rows = _board()
        assert _short(_resolve(rows, slug, title, ev, oc)) == EXPECT[slug], slug
        ex = _explain(rows, slug, title, ev, oc)
        if EXPECT[slug]:
            assert ex["step"] == "resolves" and ex["detail"] == EXPECT[slug][0]
            assert ex.get("league_alias") == EXPECT[slug][3]
        else:
            assert ex["step"] == "no_key_intersection" and ex["rows"] == 0

    @pytest.mark.parametrize("slug,title,ev,oc", FEED[5:])
    def test_marseille_and_vitoria_alias_once_the_venue_rows_exist(self, armed, slug, title, ev, oc):
        rows = _board(extra=VENUE_EXTRA)
        assert _short(_resolve(rows, slug, title, ev, oc)) == EXPECT_EXTRA[slug]

    def test_every_hit_keeps_source_premap_in_the_mirror(self, armed):
        """map_market's source for a premap answer is 'premap' whatever
        matched_by says -- the class the quarantine admits (C1 §7.4)."""
        from tests.test_c1_round2 import _premap_pool
        from tests.test_mirror_maps_the_copy_lane import _fresh, _yesno_fills  # noqa: F401
        from tests.test_mirror_shadow import CID
        rows = _board()
        for slug, title, ev, oc in FEED[:5]:
            fills = _yesno_fills(slug, title, ev, slug.rsplit("-", 1)[0])
            m = asyncio.run(ms.map_market(_premap_pool(fills, rows), fills, None, whale="rn1",
                                          condition_id=CID))
            assert m and m["source"] == "premap" and m["us_slug"] == EXPECT[slug][0], slug
            ms._map_cache.clear()

    def test_dark_the_seven_answer_byte_identically_to_before(self, dark):
        for slug, title, ev, oc in FEED:
            assert _resolve(_board(extra=VENUE_EXTRA), slug, title, ev, oc) is None, slug


# ------------------------------------------------------- 1. the league slot

class TestTheLeagueSlotIsALeagueNotAClub:
    @pytest.mark.parametrize("lg", ["serie a", "la liga", "ligue 1", "primeira liga",
                                    "liga argentina", "eliteserien", "ekstraklasa",
                                    "bundesliga", "premier league", "k league 1"])
    def test_real_league_names_pass(self, lg):
        assert pmus._yn_league_slot_bad(lg) is False

    @pytest.mark.parametrize("lg", ["serie a u21", "serie a women", "primavera", "primavera 1",
                                    "serie b", "liga f", "serie a reserves", "esoccer battle",
                                    "gt leagues", "", "a", "serie a friendly",
                                    "one two three four five six", "serie a play off"])
    def test_another_scope_refuses(self, lg):
        assert pmus._yn_league_slot_bad(lg) is True

    def test_the_club_screen_still_refuses_serie_a_and_that_is_why_it_left_the_slot(self):
        assert pmus._yn_slot_bad("serie a") is True

    def test_serie_a_maps_and_its_youth_and_womens_twins_refuse_by_name(self, armed):
        slug, title, ev, oc = FEED[0]
        rows = _board()
        assert _short(_resolve(rows, slug, title, ev, oc)) == EXPECT[slug]
        for lg in ("Serie A U21", "Serie A Women", "Primavera", "Serie B"):
            q = (f"Will Juventus FC win against AC Milan in the {lg} match scheduled for "
                 f"Sep 6, 2026?")
            bad = _with_question(rows, "atc-sea-juv-mil-2026-09-06-juv", q)
            assert _resolve(bad, slug, title, ev, oc) is None, lg
            ex = _explain(bad, slug, title, ev, oc)
            assert ex["step"] == "no_side_match" and ex["split"] == "yn:league-slot", lg
            assert ex["yn_c2"]["refusal"] == "yn:league-slot"

    def test_the_copy_lanes_yesno_lane_reads_the_same_rule(self):
        src = inspect.getsource(pmus.resolve_team_yesno_exact)
        assert "_yn_league_slot_bad(lgq)" in src and "yn:league-slot" in src


# ------------------------------------------------------ 2. names with digits

class TestNamesWithDigits:
    def test_sassuolo_maps_with_bologna_fc_1909_as_the_opponent(self, armed):
        slug, title, ev, oc = FEED[1]
        assert _short(_resolve(_board(), slug, title, ev, oc)) == EXPECT[slug]

    @pytest.mark.parametrize("opp", ["Mainz 05", "1899 Hoffenheim", "Bologna FC 1909"])
    def test_a_digit_opponent_passes_the_template_as_tokens(self, armed, opp):
        q = (f"Will Eintracht Frankfurt win against {opp} in the Bundesliga match scheduled "
             f"for Sep 6, 2026?")
        venue = {"aec-bun-fra-opp-2026-09-06": (f"Eintracht Frankfurt vs. {opp}", [
            ("atc-bun-fra-opp-2026-09-06-fra", q)])}
        h = _resolve(_board(venue), "bun-fra-opp-2026-09-06-fra",
                     "Will Eintracht Frankfurt win on 2026-09-06?",
                     f"Eintracht Frankfurt vs. {opp}", "Yes")
        assert _short(h) == ("atc-bun-fra-opp-2026-09-06-fra", LONG, "premap_identity", None)
        n = " ".join(pmus._norm_folded(q).split())
        gm = pmus._YN_Q_PATTERNS[0].fullmatch(n)
        assert gm and gm.group("opp") == pmus._norm(opp)

    def test_a_digit_is_a_token_never_a_wildcard(self):
        assert pmus._yn_name_match("bologna fc 1909", "bologna fc 1909")
        assert not pmus._yn_name_match("bologna fc 1909", "bologna fc 1919")
        assert not pmus._yn_name_match("bologna fc 1909", "bologna fc")

    def test_a_digit_in_his_own_subject_still_refuses_at_the_bridge_and_is_named(self, armed):
        """The whale-side pin (subject_has_digit, test_mapping_identity)
        is untouched: 'Will Mainz 05 win ...' refuses, now under the
        census name yn:name-digits."""
        q = ("Will Mainz 05 win against Eintracht Frankfurt in the Bundesliga match scheduled "
             "for Sep 6, 2026?")
        venue = {"aec-bun-mai-fra-2026-09-06": ("Mainz 05 vs. Eintracht Frankfurt", [
            ("atc-bun-mai-fra-2026-09-06-mai", q)])}
        rows = _board(venue)
        args = (rows, "bun-mai-fra-2026-09-06-mai", "Will Mainz 05 win on 2026-09-06?",
                "Mainz 05 vs. Eintracht Frankfurt", "Yes")
        assert _resolve(*args) is None
        ex = _explain(*args)
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:name-digits"


# ---------------------------------------------------------- 3. diacritics

FOLD = [("Tromsø IL", "tromso il"), ("Vitória SC", "vitoria sc"),
        ("Atlético Madrid", "atletico madrid"), ("SC Bragança", "sc braganca"),
        ("Malmö FF", "malmo ff"), ("1. FC Köln", "1 fc koln"),
        ("AS Saint-Étienne", "as saint etienne"), ("Örebro SK", "orebro sk"),
        ("ŁKS Łódź", "lks lodz"), ("CA Newell's Old Boys", "ca newells old boys"),
        ("Bodø/Glimt", "bodo glimt"), ("Đorđe Petrović", "dorde petrovic")]


class TestDiacriticsFoldToTheVenuesAscii:
    @pytest.mark.parametrize("raw,want", FOLD)
    def test_the_fold(self, raw, want):
        assert " ".join(pmus._norm_folded(raw).split()) == want
        assert premap._folds_away(raw) is False

    def test_the_letters_nfkd_drops_were_dropped_before_and_are_mapped_now(self):
        assert pmus._norm("Tromsø IL") == "troms il"          # the old reading, kept in _norm
        assert pmus._norm("ŁKS Łódź") == "ks odz"
        assert pmus.fold_latin("Tromsø Łódź ß") == "Tromso Lódź ss"   # the map alone
        assert pmus._norm_folded("Tromsø Łódź ß") == "tromso lodz ss"  # then NFKD

    def test_a_letter_outside_the_table_still_refuses_by_name(self):
        for raw in ("Will NEOM SC win (Παράταση)?", "Will Ƹ win?", "Will X win (٢)?"):
            assert premap._folds_away(raw) is True
        assert premap._yn_title_is_his_game("Will NEOM SC win (Παράταση)?",
                                            "spl-neo-kha-2026-09-03-neo") is False

    def test_norm_itself_is_untouched(self):
        """side_norm is half of the us_premap unique index: the global
        fold is not changed, only the yes/no name channels and the keys."""
        src = inspect.getsource(pmus._norm)
        assert "fold_latin" not in src and "_LATIN_FOLD" not in src

    def test_his_event_title_keys_meet_the_venues_ascii_keys(self):
        his = set(premap.event_keys_for("Kristiansund BK vs. Tromsø IL", "nor-kbk-tro-2026-09-06"))
        venue = set(premap.event_keys_for("Kristiansund BK vs. Tromso IL",
                                          "aec-els-kbk-tro-2026-09-06"))
        assert "kristiansund bk vs tromso il@2026-09-06" in his & venue
        assert not any("troms il" in k for k in his)

    def test_tromso_maps_through_the_fold_and_the_alias(self, armed):
        slug, title, ev, oc = FEED[3]
        assert _short(_resolve(_board(), slug, title, ev, oc)) == EXPECT[slug]
        assert premap._bridge_title_subject(title, slug) == ("tromso il", "ok")

    def test_the_copy_lanes_lane_folds_the_same_way(self, monkeypatch):
        """pmus.resolve_team_yesno_exact reads his pick, his titles and
        the venue question through the same fold, so 'Tromsø' is never
        a different name on the two sides of that lane either."""
        q = ("Will Tromsø IL win against Bodø/Glimt in the Eliteserien match scheduled for "
             "Sep 6, 2026?")

        class _M:
            def retrieve_by_slug(self, slug):
                return {"market": {"slug": slug, "question": q,
                                   "eventSlug": "els-bod-tro-2026-09-06",
                                   "marketSides": _market(slug, "")["marketSides"]}}

        class _C:
            markets = _M()

        monkeypatch.setattr(pmus, "_get_client", lambda: _C())
        d: list = []
        h = pmus.resolve_team_yesno_exact("els-bod-tro-2026-09-06-tro", "Tromsø IL",
                                          "Tromsø IL", "Bodø/Glimt vs. Tromsø IL", d)
        assert h is not None and h["market_slug"] == "atc-els-bod-tro-2026-09-06-tro", d
        # the lane's OWN gates are untouched: an opponent whose name does
        # not prefix its code still refuses there (yn:opp-title-code)
        d2: list = []
        assert pmus.resolve_team_yesno_exact("els-kbk-tro-2026-09-06-tro", "Tromsø IL",
                                             "Tromsø IL", "Kristiansund BK vs. Tromsø IL",
                                             d2) is None
        assert d2 == ["yn:opp-title-code"]


# ---------------------------------------------------- 4. the league alias

Q_TRO = VENUE["aec-els-kbk-tro-2026-09-06"][1][1][1]
S_TRO, T_TRO, EV_TRO = "nor-kbk-tro-2026-09-06-tro", FEED[3][1], FEED[3][2]
ID_ELS_TRO = "atc-els-kbk-tro-2026-09-06-tro"


class TestTheLeagueAlias:
    def test_nor_to_els_and_arg_to_lpa_on_the_dump(self, armed):
        rows = _board()
        for i in (3, 4):
            slug, title, ev, oc = FEED[i]
            assert _short(_resolve(rows, slug, title, ev, oc)) == EXPECT[slug]

    def test_pol_to_ekst_with_polish_letters_on_both_names(self, armed):
        q = ("Will Jagiellonia Białystok win against Śląsk Wrocław in the Ekstraklasa match "
             "scheduled for Sep 6, 2026?")
        venue = {"aec-ekst-jag-sla-2026-09-06": ("Jagiellonia Bialystok vs. Slask Wroclaw", [
            ("atc-ekst-jag-sla-2026-09-06-jag", q),
            ("atc-ekst-jag-sla-2026-09-06-sla",
             "Will Śląsk Wrocław win against Jagiellonia Białystok in the Ekstraklasa match "
             "scheduled for Sep 6, 2026?")])}
        rows = _board(venue)
        y = _resolve(rows, "pol-jag-sla-2026-09-06-jag", "Will Jagiellonia Białystok win on 2026-09-06?",
                     "Jagiellonia Białystok vs. Śląsk Wrocław", "Yes")
        n = _resolve(rows, "pol-jag-sla-2026-09-06-jag", "Will Jagiellonia Białystok win on 2026-09-06?",
                     "Jagiellonia Białystok vs. Śląsk Wrocław", "No")
        assert _short(y) == ("atc-ekst-jag-sla-2026-09-06-jag", LONG, "premap_alias", "pol->ekst")
        assert _short(n) == ("atc-ekst-jag-sla-2026-09-06-jag", SHORT, "premap_alias", "pol->ekst")
        # and the OTHER team's slug takes the other contract, never his
        s = _resolve(rows, "pol-jag-sla-2026-09-06-sla", "Will Śląsk Wrocław win on 2026-09-06?",
                     "Jagiellonia Białystok vs. Śląsk Wrocław", "Yes")
        assert s["market_slug"] == "atc-ekst-jag-sla-2026-09-06-sla"

    def test_no_returns_the_no_row_short(self, armed):
        h = _resolve(_board(), S_TRO, T_TRO, EV_TRO, "No")
        assert _short(h) == (ID_ELS_TRO, SHORT, "premap_alias", "nor->els")
        assert h["outcome"] == "no"

    def test_a_suffix_carried_by_two_league_codes_refuses(self, armed):
        """A same-day twin with the same team codes under another venue
        league (women's, youth, esoccer ...): the branch cannot say
        which is his and refuses, even though the twin's own league
        slot would refuse it."""
        twin = {"aec-elsw-kbk-tro-2026-09-06": ("Kristiansund BK vs. Tromso IL", [
            ("atc-elsw-kbk-tro-2026-09-06-tro",
             "Will Tromso IL win against Kristiansund BK in the Eliteserien Women match "
             "scheduled for Sep 6, 2026?")])}
        rows = _board(extra=twin)
        assert _resolve(rows, S_TRO, T_TRO, EV_TRO, "Yes") is None
        ex = _explain(rows, S_TRO, T_TRO, EV_TRO, "Yes")
        assert ex["split"] == "yn:league-ambiguous"
        assert ex["yn_c2"]["league_codes"] == ["els", "elsw"]
        # the twin alone (his real league unlisted) refuses at the slot
        assert _resolve(_board({}, twin), S_TRO, T_TRO, EV_TRO, "Yes") is None
        assert _explain(_board({}, twin), S_TRO, T_TRO, EV_TRO, "Yes")["split"] == "yn:league-slot"

    def test_a_wrong_team_title_on_the_right_suffix_refuses_the_neom_shear(self, armed):
        """His slug says -tro, his title asks about Kristiansund: the
        alias arm refuses exactly as the identity arm does."""
        rows = _board()
        t_kbk = "Will Kristiansund BK win on 2026-09-06?"
        assert _resolve(rows, S_TRO, t_kbk, EV_TRO, "Yes") is None
        assert _explain(rows, S_TRO, t_kbk, EV_TRO, "Yes")["split"] == "yn:subj"
        assert premap.match_side(rows, "Yes", t_kbk, S_TRO, yn_identity=True,
                                 his_event_title=EV_TRO) is None
        # the identity arm's own NEOM pin, unchanged
        from tests.test_mapping_identity import S_KHA, T_NEO, _board as _neom
        assert premap.match_side(_neom(), "Yes", T_NEO, S_KHA, yn_identity=True,
                                 his_event_title="NEOM SC vs. Al Khaleej Saudi Club") is None

    def test_the_alias_needs_his_event_title_as_the_witness(self, armed):
        rows = _board()
        for ev in (None, "", "Tromsø IL", "A vs B vs C", "Tromsø IL vs. B"):
            assert _resolve(rows, S_TRO, T_TRO, ev, "Yes") is None, ev
            # without his event title the keys never fetch the venue's
            # rows at all; handed the rows, the branch names the gap
            assert _explain(rows, S_TRO, T_TRO, ev, "Yes")["step"] == "no_key_intersection"
            tr: dict = {}
            assert premap._yn_pick(rows, "Yes", T_TRO, S_TRO, ev, tr) == []
            assert tr["refusal"] == "yn:alias-unwitnessed", ev
        # an event title naming another game is shear (handed the rows:
        # such a title's keys would never have fetched them)
        for ev, why in (("Bodø/Glimt vs. Tromsø IL", "yn:opp-witness"),
                        ("Kristiansund BK vs. Bodø/Glimt", "yn:event-shear"),
                        ("Tromsø IL vs. Tromsø IL", "yn:event-shear")):
            tr = {}
            assert premap._yn_pick(rows, "Yes", T_TRO, S_TRO, ev, tr) == []
            assert tr["refusal"] == why, ev
            assert _resolve(rows, S_TRO, T_TRO, ev, "Yes") is None
        # the four-argument identity form carries no witness: no alias
        assert premap.yn_identity_rows(rows, "Yes", T_TRO, S_TRO) == []
        assert premap.match_side(rows, "Yes", T_TRO, S_TRO, yn_identity=True) is None

    def test_his_own_identifier_on_the_board_and_refused_is_never_aliased_around(self, armed):
        own = {"aec-nor-kbk-tro-2026-09-06": ("Kristiansund BK vs. Tromso IL", [
            ("atc-nor-kbk-tro-2026-09-06-tro",
             "Will Tromso IL win against Kristiansund BK in the Eliteserien Women match "
             "scheduled for Sep 6, 2026?")])}
        rows = _board(extra=own)
        assert _resolve(rows, S_TRO, T_TRO, EV_TRO, "Yes") is None
        assert _explain(rows, S_TRO, T_TRO, EV_TRO, "Yes")["split"] == "yn:league-slot"

    def test_the_team_order_must_be_his_and_the_date_his(self, armed):
        rows = _board()
        assert _resolve(rows, "nor-tro-kbk-2026-09-06-tro", T_TRO, EV_TRO, "Yes") is None
        assert _resolve(rows, "nor-kbk-tro-2026-09-07-tro",
                        "Will Tromsø IL win on 2026-09-07?", EV_TRO, "Yes") is None
        rows7 = _with_question(rows, ID_ELS_TRO, Q_TRO.replace("Sep 6", "Sep 7"))
        assert _explain(rows7, S_TRO, T_TRO, EV_TRO, "Yes")["split"] == "yn:qdate"

    def test_the_existing_row_gates_hold_on_an_alias_row(self, armed):
        rows = _board()
        swapped = [dict(r, intent=SHORT if r["intent"] == LONG else LONG) for r in rows]
        assert _resolve(swapped, S_TRO, T_TRO, EV_TRO, "Yes") is None
        assert _explain(swapped, S_TRO, T_TRO, EV_TRO, "Yes")["split"] == "yn:intent"
        lined = [dict(r, line="1.5") for r in rows]
        assert _resolve(lined, S_TRO, T_TRO, EV_TRO, "Yes") is None
        assert _resolve(rows, S_TRO, "Will Tromsø IL win -1.5 on 2026-09-06?", EV_TRO,
                        "Yes") is None
        terse = _with_question(rows, ID_ELS_TRO, "Will Tromso IL win?")
        assert _explain(terse, S_TRO, T_TRO, EV_TRO, "Yes")["split"] == "yn:shape"
        two = rows + [dict(r) for r in rows if r["identifier"] == ID_ELS_TRO]
        assert _resolve(two, S_TRO, T_TRO, EV_TRO, "Yes") is None, "two agreeing rows: ambiguous"

    def test_no_static_table_of_aliases_exists(self):
        src = inspect.getsource(premap._yn_alias_pick) + inspect.getsource(premap._yn_pick)
        for code in ("'els'", '"els"', "'lpa'", "'ekst'", "'nor'", "'arg'", "'pol'"):
            assert code not in src
        assert "_yn_rows_by_code" in src

    def test_the_alias_rides_the_hit_and_the_explain(self, armed):
        h = _resolve(_board(), S_TRO, T_TRO, EV_TRO, "Yes")
        assert h["league_alias"] == "nor->els" and h["matched_by"] == "premap_alias"
        ex = _explain(_board(), S_TRO, T_TRO, EV_TRO, "Yes")
        assert ex["step"] == "resolves" and ex["league_alias"] == "nor->els"

    def test_the_pure_functions_stay_pure(self):
        for fn in (premap._yn_pick, premap._yn_alias_pick, premap._yn_draw_pick,
                   premap.yn_identity_rows):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv"):
                assert forbidden not in src, (fn.__name__, forbidden)
        assert list(inspect.signature(premap.yn_identity_rows).parameters) == [
            "rows", "outcome", "his_title", "his_slug"]

    def test_the_wording_arm_never_reads_the_event_title(self):
        src = inspect.getsource(premap.match_side)
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        i = code.index("cands = [r for r in rows")
        assert "his_event_title" not in code[i:code.index("if not cands and yn_identity")]


# ------------------------------------------------------------- 5. the draw

S_DRAW, EV_JM = "sea-juv-mil-2026-09-06-draw", "Juventus FC vs. AC Milan"
ID_DRAW = "atc-sea-juv-mil-2026-09-06-draw"
T_DRAW = "Will Juventus FC vs. AC Milan end in a draw on 2026-09-06?"


class TestTheDraw:
    @pytest.mark.parametrize("t", [T_DRAW, "Will the match end in a draw?",
                                   "Will AC Milan vs. Juventus FC end in a draw?",
                                   "Will there be a draw between Juventus FC and AC Milan "
                                   "on Sep 6, 2026?",
                                   "Juventus FC vs AC Milan: Draw"])
    def test_his_draw_slug_takes_the_venues_draw_contract(self, armed, t):
        rows = _board()
        y = _resolve(rows, S_DRAW, t, EV_JM, "Yes")
        n = _resolve(rows, S_DRAW, t, EV_JM, "No")
        assert _short(y) == (ID_DRAW, LONG, "premap_identity", None), t
        assert _short(n) == (ID_DRAW, SHORT, "premap_identity", None), t

    def test_either_order_of_his_event_title(self, armed):
        assert _resolve(_board(), S_DRAW, T_DRAW, "AC Milan vs. Juventus FC", "Yes")["market_slug"] == ID_DRAW

    def test_the_draw_under_an_alias(self, armed):
        h = _resolve(_board(), "nor-kbk-tro-2026-09-06-draw",
                     "Will Kristiansund BK vs. Tromsø IL end in a draw on 2026-09-06?",
                     "Kristiansund BK vs. Tromsø IL", "Yes")
        assert _short(h) == ("atc-els-kbk-tro-2026-09-06-draw", LONG, "premap_alias", "nor->els")

    @pytest.mark.parametrize("t,why", [
        ("Will Juventus FC vs. AC Milan end in a draw at halftime?", "qualifier"),
        ("Will Juventus FC win or draw on 2026-09-06?", "not_draw_shape"),
        ("Will Juventus FC vs. AC Milan end in a draw on 2026-09-07?", "date_mismatch"),
        ("Will Juventus FC vs. AC Milan end in a draw on Sep 7, 2026?", "date_mismatch"),
        ("Will Juventus FC vs. AC Milan end in a draw on 9/6/2026?", "date_unreadable"),
        ("Will Juventus FC vs. AC Milan (Aggregate) end in a draw?", "qualifier"),
        ("Draw no bet: Juventus FC", "qualifier"),
        ("Will Juventus FC win on 2026-09-06?", "not_draw_shape"),
        ("Will Juventus FC vs. AC Milan end in a draw (Παράταση)?", "title-folds"),
        ("", "draw-title-folds"),
        (None, "draw-title-folds"),
    ])
    def test_a_title_that_is_not_his_draw_question_refuses_by_name(self, armed, t, why):
        rows = _board()
        assert _resolve(rows, S_DRAW, t, EV_JM, "Yes") is None, t
        want = f"yn:{why}" if why.endswith("folds") else f"yn:draw-title-{why}"
        assert _explain(rows, S_DRAW, t, EV_JM, "Yes")["split"] == want, t

    def test_the_venues_teams_must_be_his_two(self, armed):
        rows = _board()
        # his event says another game: the draw row's names do not match
        assert _resolve(rows, S_DRAW, T_DRAW, "Bologna FC 1909 vs. US Sassuolo Calcio",
                        "Yes") is None
        ex = _explain(rows, S_DRAW, "Will the match end in a draw?",
                      "Bologna FC 1909 vs. US Sassuolo Calcio", "Yes")
        assert ex["split"] == "yn:draw-names"
        # and no event title means no witness
        assert _explain(rows, S_DRAW, T_DRAW, None, "Yes")["split"] == "yn:draw-unwitnessed"
        # a per-team question on the draw identifier is not a draw
        bad = _with_question(rows, ID_DRAW, VENUE["aec-sea-juv-mil-2026-09-06"][1][0][1])
        assert _explain(bad, S_DRAW, T_DRAW, EV_JM, "Yes")["split"] == "yn:shape"

    def test_a_draw_slug_never_takes_a_per_team_row_and_vice_versa(self, armed):
        rows = _board()
        h = _resolve(rows, S_DRAW, T_DRAW, EV_JM, "Yes")
        assert h["market_slug"] == ID_DRAW
        # his per-team slug with a draw title refuses at the title gate
        assert _resolve(rows, FEED[0][0], T_DRAW, EV_JM, "Yes") is None
        # the venue's draw pattern is not one of the per-team patterns
        assert len(pmus._YN_Q_PATTERNS) == 1
        assert pmus._YN_DRAW_Q_RE.pattern not in [p.pattern for p in pmus._YN_Q_PATTERNS]

    def test_the_draw_stays_dark_with_the_flag_off(self, dark):
        assert _resolve(_board(), S_DRAW, T_DRAW, EV_JM, "Yes") is None
        assert premap.match_side(_board(), "Yes", T_DRAW, S_DRAW) is None


# ----------------------------------------------- 6. the census names them

class TestTheCensusNamesEveryRefusal:
    def test_explain_unmapped_carries_the_name_in_the_split(self, armed):
        rows = _board(extra={"aec-elsw-kbk-tro-2026-09-06": ("Kristiansund BK vs. Tromso IL", [
            ("atc-elsw-kbk-tro-2026-09-06-tro",
             "Will Tromso IL win against Kristiansund BK in the Eliteserien Women match "
             "scheduled for Sep 6, 2026?")])})
        ctx = {"title": T_TRO, "event_title": EV_TRO, "outcome": "Yes", "his_slug": S_TRO}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == \
            "no_side_match:yn:league-ambiguous"

    def test_dark_a_would_resolve_reads_as_such(self, dark):
        ex = _explain(_board(), S_TRO, T_TRO, EV_TRO, "Yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:would-resolve"
        assert ex["yn_identity"]["would_resolve"] is True
        assert ex["yn_c2"] == {"matched_by": "premap_alias", "league_alias": "nor->els",
                               "admitted": frozenset({ID_ELS_TRO})}

    def test_the_identity_probes_pinned_dict_is_unchanged(self, dark):
        from tests.test_mapping_identity import ID_NEO, S_NEO, T_NEO, _board as _neom
        ex = _explain(_neom(), S_NEO, T_NEO, "NEOM SC vs. Al Khaleej Saudi Club", "Yes")
        assert ex["yn_identity"] == {"on": False, "would_resolve": True, "identifier": ID_NEO,
                                     "side_norm": "yes", "intent": LONG}

    def test_a_named_pick_carries_no_yn_split(self, armed):
        ex = _explain(_board(), FEED[0][0], FEED[0][1], FEED[0][2], "Juventus FC")
        assert ex["step"] == "no_side_match" and "split" not in ex

    def test_the_refusal_names_are_closed(self):
        src = inspect.getsource(premap)
        names = set()
        import re as _re
        for m in _re.finditer(r'"(yn:[a-z-]+)"', src):
            names.add(m.group(1))
        for m in _re.finditer(r'f"yn:([a-z-]+)-\{', src):
            names.add("yn:" + m.group(1) + "-*")
        assert {"yn:league-slot", "yn:league-ambiguous", "yn:name-digits", "yn:no-row",
                "yn:alias-unwitnessed", "yn:event-shear", "yn:opp-witness", "yn:draw-names",
                "yn:draw-unwitnessed", "yn:shape", "yn:qdate", "yn:scope", "yn:subj",
                "yn:side", "yn:intent", "yn:folds", "yn:title-folds", "yn:identity-refused",
                "yn:title-*", "yn:draw-title-*"} <= names


# --------------------------------------------- 7. the unmapped memo (shadow)

class TestTheUnmappedMemoOnFirstSight:
    def test_the_rule(self):
        f = ms.unmapped_memo_s
        assert f("no_key_intersection", seen_before=False) == ms.UNMAPPED_FRESH_TTL_S
        assert f("no_side_match", seen_before=False) == ms.UNMAPPED_FRESH_TTL_S
        assert f("no_side_match:yn:league-slot", seen_before=False) == ms.UNMAPPED_FRESH_TTL_S
        assert f("no_side_match", seen_before=True) == ms.UNMAPPED_TTL_S
        assert f("no_key_intersection", seen_before=True) == ms.UNMAPPED_TTL_S
        for other in ("unknown_market_type:unparsed", "type_prefix_filter_emptied",
                      "no_date_on_his_signal", "side_code_unmatched", None, ""):
            assert f(other, seen_before=False) == ms.UNMAPPED_TTL_S, other
        assert 0 < ms.UNMAPPED_FRESH_TTL_S < ms.UNMAPPED_TTL_S
        assert ms.UNMAPPED_FRESH_TTL_S == 2 * premap.FAST_REFRESH_SECONDS

    def test_the_tick_writes_the_memo_through_the_rule(self):
        src = inspect.getsource(ms.tick_once)
        i = src.index('startswith("unmapped")')
        assert "unmapped_memo_s(" in src[i:i + 300]
        assert "seen_before=(w, cid) in _unmapped_until" in src[i:i + 300]
        # the UNMAPPED memo never bypasses the rule (the terminal memo
        # beside it, W1 / R2, is a flat UNMAPPED_TTL_S by design)
        assert "_unmapped_until[(w, cid)] = now_ts + UNMAPPED_TTL_S" not in src

    def test_first_sight_then_the_full_memo(self, monkeypatch):
        """Two shadow ticks on one unmapped market: the first verdict is
        remembered for the fresh memo, the second for the full one."""
        from tests.test_mirror_shadow import CID, HIS, _Pmus, _nosleep, _Pool as _SPool
        _nosleep(monkeypatch)
        monkeypatch.setenv("MIRROR_WHALES", "rn1")
        monkeypatch.setattr(ms, "_unmapped_until", {})
        monkeypatch.setattr(ms, "_map_cache", {})
        ms._ratio_cache.update(at=0.0, by_whale={})
        ms._backoff_until = 0.0

        async def _explain_stub(pool, ctx, refusal=None):
            return "no_key_intersection"

        monkeypatch.setattr(ms, "explain_unmapped", _explain_stub)
        p = _SPool(fills=HIS, mapped=False)
        s1 = asyncio.run(ms.tick_once(p, _Pmus(), now_ts=5000.0))
        assert s1["unmapped"] == 1
        assert ms._unmapped_until[("rn1", CID)] == 5000.0 + ms.UNMAPPED_FRESH_TTL_S
        s2 = asyncio.run(ms.tick_once(p, _Pmus(), now_ts=5000.0 + ms.UNMAPPED_FRESH_TTL_S + 1))
        assert s2["unmapped"] == 1
        assert ms._unmapped_until[("rn1", CID)] == \
            5000.0 + ms.UNMAPPED_FRESH_TTL_S + 1 + ms.UNMAPPED_TTL_S
