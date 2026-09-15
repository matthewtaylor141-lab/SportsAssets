"""M1 (2026-09-07): seven small identity fixes the investigators proved on
the venue's verbatim rows (gap_new_families.md, gap_soccer.md,
gap_prefix_filter.md), each pinned here on those rows:

  1. the event-title KEY grammar -- his stored `markets.event_title`
     carries a trailing family segment ('… - Exact Score', '… - Halftime
     Result', '… - More Markets') and his exact-score title names the two
     clubs around the score; both key the matchup (lookup only);
  2. a lone post-date token EQUAL to one of his slug's own two team codes
     is a moneyline ('chi1-cdp-cu1-…-cu1'), any other alnum token stays
     unknown;
  3. the league slot admits the letter after 'serie' ('Serie B', the
     venue's own phrase);
  4. the empty prefix filter is named by the board: '<family>:kind-absent:
     league-unlisted' / ':league-listed' from a read-only probe of the
     venue's own identifiers (resolve_explain only);
  5. a spread whose wanted identifier is listed with no yes/no side is
     'spread:sides-unparsed', not 'spread:line-absent' (reporting only);
  6. (P6) a numbered club ('Bologna FC 1909') maps when the venue's own
     per-team question restates the number verbatim -- raw token-set
     equality; else yn:name-digits as before; the bridge's digit rule is
     untouched;
  7. (P7) the club-slot screen's single-character rule is letters-only,
     so the '1' of '1. FSV Mainz 05' is not a scope letter.

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import pmus
from sportsassets.copy_sports import _feed_family, family_of, market_type_of
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests import test_c2_yesno_alias as c2
from tests import test_c5_code_translation as c5

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"
EVE_MNU = "Everton FC vs. Manchester United FC"
ARS_CFC = "Arsenal FC vs. Chelsea FC"


def _yn(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _tsc(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Over", "long": True},
        {"identifier": ident, "description": "Under", "long": False}]}


def _board(venue: dict) -> list[dict]:
    """Rows built by the sweep's own row builder and keyed as the sweep
    keys them (event_keys_for on the venue's event title + slug)."""
    rows: list[dict] = []
    for ev_slug, (ev_title, mkts) in venue.items():
        keys = premap.event_keys_for(ev_title, ev_slug)
        for m in mkts:
            for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m):
                r["event_keys"] = keys
                rows.append(r)
    return rows


class _Pool:
    """us_premap by key intersection; the M1 kind probe (`LIKE`) answers
    from `listed`; `trades`/`markets` titles by slug (C5's witness)."""

    def __init__(self, rows, *, listed=(), titles=None):
        self.rows = rows
        self.listed = tuple(listed)
        self.titles = titles or {}
        self.asked: list[str] = []

    async def fetch(self, sql, *a):
        if "LIKE" in sql:
            assert "us_premap" in sql and "LIMIT 1" in sql
            self.asked.append(a[0])
            return [{"?column?": 1}] if a[0] in self.listed else []
        if "us_premap" in sql:
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        return [{"t": t} for t in self.titles.get(a[0], [])]


def _resolve(pool, slug, title, ev, oc):
    return asyncio.run(premap.resolve(pool, title, ev, oc, slug))


def _explain(pool, slug, title, ev, oc):
    return asyncio.run(premap.resolve_explain(pool, title, ev, oc, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"], h.get("league_alias"))


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _with_question(rows, ident, q):
    return [dict(r, question=q) if r["identifier"] == ident else dict(r) for r in rows]


# ------------------------------------------ 1. the event-title key grammar

S_ES = f"epl-eve-mun-{D}-exact-score-2-1"
T_ES = "Exact Score: Everton FC 2 - 1 Manchester United FC?"
# the plain title's keys today (new_families_repro.log §H), byte for byte
PLAIN_KEYS = [f"epl-eve-mun-{D}", f"eve-mun-{D}", "everton fc vs manchester united fc",
              f"everton fc vs manchester united fc@{D}",
              "manchester united fc vs everton fc",
              f"manchester united fc vs everton fc@{D}"]


class TestTheEventTitleKeyGrammar:
    def test_the_plain_matchup_title_keys_byte_identically_to_today(self):
        assert premap.event_keys_for(EVE_MNU, S_ES) == PLAIN_KEYS
        assert premap._yn_event_sides(EVE_MNU) == ["everton fc", "manchester united fc"]

    @pytest.mark.parametrize("title", [f"{EVE_MNU} - Exact Score", f"{EVE_MNU} - More Markets"])
    def test_the_trailing_family_segment_keys_the_head(self, title):
        # nf_his_1350.log: every exact-score / halftime row's stored event title
        assert premap.event_keys_for(title, S_ES) == PLAIN_KEYS
        assert premap._yn_event_sides(title) == ["everton fc", "manchester united fc"]

    def test_the_halftime_and_more_markets_titles(self):
        keys = premap.event_keys_for(f"{ARS_CFC} - Halftime Result", f"epl-ars-che-{D}-halftime-result-away")
        assert f"arsenal fc vs chelsea fc@{D}" in keys and f"chelsea fc vs arsenal fc@{D}" in keys
        assert not any("halftime" in k for k in keys)
        assert premap._yn_event_sides(f"{ARS_CFC} - Halftime Result") == ["arsenal fc", "chelsea fc"]
        t = "Málaga CF vs. Levante UD - More Markets"
        keys = premap.event_keys_for(t, f"lal-mala-lev-{D}-team-total-home-2pt5")
        assert keys == [f"lal-mala-lev-{D}", "levante ud vs malaga cf", f"levante ud vs malaga cf@{D}",
                        f"mala-lev-{D}", "malaga cf vs levante ud", f"malaga cf vs levante ud@{D}"]
        # was ['malaga cf', 'levante ud more markets'] (repro H)
        assert premap._yn_event_sides(t) == ["malaga cf", "levante ud"]

    def test_his_exact_score_title_keys_the_two_clubs(self):
        # repro G: the title keyed 'everton fc 2 1 manchester united fc' and met nothing
        keys = premap.event_keys_for(T_ES, S_ES)
        assert keys == PLAIN_KEYS
        assert not any("2 1" in k for k in keys)

    def test_a_title_naming_one_club_builds_no_matchup_key_and_never_a_bare_date(self):
        for t in ("Everton FC - Exact Score", "Exact Score: Everton FC 2 - 1?"):
            keys = premap.event_keys_for(t, S_ES)
            assert not any(" vs " in k for k in keys), t
            assert D not in keys and f"@{D}" not in keys, t
        assert premap.event_keys_for("Everton FC - Exact Score", S_ES) == [
            f"epl-eve-mun-{D}", f"eve-mun-{D}", "everton fc exact score", f"everton fc exact score@{D}"]
        assert premap._yn_event_sides("Everton FC - Exact Score") is None

    def test_a_segment_with_a_digit_and_the_esports_title_are_untouched(self):
        # only letters and spaces fold away: 'Game 2' is not this grammar
        keys = premap.event_keys_for(f"{EVE_MNU} - Game 2", S_ES)
        assert f"everton fc vs manchester united fc game 2@{D}" in keys
        assert f"everton fc vs manchester united fc@{D}" not in keys
        t = "Counter-Strike: K27 vs Sinners (BO3)"
        assert premap.event_keys_for(t, f"cs2-k271-sin2-{D}-game2") == [
            f"cs2-k271-sin2-{D}", "k27 vs sinners", f"k27 vs sinners@{D}", f"k271-sin2-{D}", "sinners vs k27",
            f"sinners vs k27@{D}"]

    def test_repro_j_the_stored_title_now_reaches_the_rows(self, armed):
        """With the stored '… - Exact Score' title the exact score died at
        no_key_intersection (repro J, keys 4 rows 0); it now reaches the
        venue's rows. C6 (M2): the exact-score lane then maps it -- the
        C5 translation certified by this event title's head (mun -> mnu)
        and the venue's own question ('Will EVE vs MNU finish EVE wins
        2-1?') certifying the digit order; the halftime result, whose
        -fh- rows this board does not list, still refuses by the family
        name."""
        rows = c5._board()
        p = _Pool(rows)
        ex = _explain(p, S_ES, T_ES, f"{EVE_MNU} - Exact Score", "Yes")
        assert ex["step"] == "resolves" and ex["rows"] > 0
        assert ex["matched_by"] == "premap_exact_score" and ex["code_translated"] == {"mun": "mnu"}
        assert ex["yn_c5"]["witness_src"] == "event"
        h = _resolve(p, S_ES, T_ES, f"{EVE_MNU} - Exact Score", "Yes")
        assert _short(h) == (f"atc-epl-eve-mnu-{D}-exact-score-2-1", "yes", LONG,
                             "premap_exact_score", None)
        # his exact-score title alone (no stored event title) fetches them
        # too, but nothing witnesses mun -> mnu: C5 refuses, the lane's own
        # reading of his untranslated codes (family-absent) beside it
        ex = _explain(p, S_ES, T_ES, None, "Yes")
        assert ex["rows"] > 0 and ex["step"] == "no_side_match"
        assert ex["split"] == "yn:code-translate:unwitnessed"
        assert ex["c6"]["refusal"] == "exact:family-absent"
        assert _resolve(p, S_ES, T_ES, None, "Yes") is None
        slug = f"epl-ars-che-{D}-halftime-result-away"
        ex = _explain(p, slug, "Chelsea FC leading at halftime?", f"{ARS_CFC} - Halftime Result", "Yes")
        assert ex["step"] == "unknown_market_type" and ex["refusal"] == "halftime:family-absent"
        assert ex["rows"] > 0
        assert _resolve(p, slug, "Chelsea FC leading at halftime?", f"{ARS_CFC} - Halftime Result", "Yes") is None

    def test_c5s_event_witness_reads_the_same_head(self, armed):
        """His stored '- More Markets' title witnesses the shared code for
        C5 exactly as the plain title does (the event witness was dark:
        _yn_event_sides read 'manchester united fc more markets')."""
        rows = c5._board()
        slug, title = f"epl-eve-mun-{D}-eve", "Will Everton FC win on 2026-09-06?"
        h = _resolve(_Pool(rows), slug, title, f"{EVE_MNU} - More Markets", "Yes")
        assert _short(h) == (f"atc-epl-eve-mnu-{D}-eve", "yes", LONG, "premap_identity", None)
        assert h["code_translated"] == {"mun": "mnu"}
        ex = _explain(_Pool(rows), slug, title, f"{EVE_MNU} - Exact Score", "Yes")
        assert ex["step"] == "resolves" and ex["yn_c5"]["witness_src"] == "event"


# -------------------------------------------- 2. the digit team code

class TestTheDigitTeamCodeIsAMoneyline:
    @pytest.mark.parametrize("slug,want", [
        (f"chi1-cdp-cu1-{D}-cu1", "moneyline"),       # nf_his: $7,122, unknown_market_type:unparsed
        (f"chi1-cdp-cu1-{D}-cu2", "unknown"),         # not one of his codes
        (f"arg-riv-riv1-{D}-riv1", "moneyline"),
        (f"bra-cor1-cha-{D}-cor1", "moneyline"),
        (f"ere-her1-az-{D}-her1", "moneyline"),
        (f"cs2-fnc-nip-{D}-game2", "unknown"),        # market_type_of unchanged; family_of is E2's map_winner
        (f"chi1-cdp-cu1-{D}-cdp1", "unknown"),
        (f"chi1-cdp-cu1-{D}-chi1", "unknown"),        # the league code is not a team
    ])
    def test_the_lone_token_must_equal_one_of_his_two_codes(self, slug, want):
        assert market_type_of(slug) == want
        # E2 (M4) files his game<N> as the map-winner family; every other
        # unknown stays unknown
        assert family_of(slug) == ("map_winner" if slug.endswith("-game2") else want)
        assert premap.slug_lines(slug) == set()
        if slug.endswith("-game2"):
            assert _feed_family(slug) == ("map_winner", "map2")
        else:
            assert _feed_family(slug) is None

    def test_the_alpha_rule_and_the_rest_of_the_parser_are_unchanged(self):
        assert market_type_of(f"uslc-tul-srp-{D}-srp") == "moneyline"
        assert market_type_of(f"mlb-tor-hou-{D}-weird-99x") == "unknown"
        assert market_type_of(f"nhl-tor-mtl-{D}-tor-1pt5") == "spread"

    def test_chi1_refuses_at_the_c2_and_c5_arms_by_name(self, armed):
        """nf_his: 'chi1-cdp-cu1-2026-09-06-cu1 | Will CD Universidad de
        Concepción win on 2026-09-06? | CD Palestino vs. CD Universidad de
        Concepción'; nf_venue_1350.log:385-390: the venue's atc-pdc-pal-uco
        rows (questions verbatim to the log's 100-char cut, completed by
        the P4 template; the venue's event title as its questions name
        the clubs -- the lookup key only). BOTH codes and the league
        differ: the identity arm has no row, C5 cannot certify."""
        pdc = "Primera Chile"
        venue = {f"pdc-pal-uco-{D}": ("CD Palestino vs. CD Universidad de Concepcion", [
            _yn(f"atc-pdc-pal-uco-{D}-pal",
                f"Will CD Palestino win against CD Universidad de Concepcion in the {pdc} match "
                f"scheduled for Sep 6, 2026?"),
            _yn(f"atc-pdc-pal-uco-{D}-uco",
                f"Will CD Universidad de Concepcion win against CD Palestino in the {pdc} match "
                f"scheduled for Sep 6, 2026?"),
            _yn(f"atc-pdc-pal-uco-{D}-draw",
                f"Will the {pdc} match CD Palestino vs CD Universidad de Concepcion scheduled for "
                f"Sep 6, 2026 end in a draw?"),
        ])}
        p = _Pool(_board(venue))
        args = (f"chi1-cdp-cu1-{D}-cu1", "Will CD Universidad de Concepción win on 2026-09-06?",
                "CD Palestino vs. CD Universidad de Concepción", "Yes")
        # C6-N (M3): the identity and alias arms still find no row and C5
        # still cannot certify; the names-witnessed lane then maps it on the
        # venue's own question naming his club and his opponent
        hit = _resolve(p, *args)
        assert hit is not None and hit["market_slug"] == f"atc-pdc-pal-uco-{D}-uco"
        ex = _explain(p, *args)
        assert ex["rows"] == 6                                   # the rows were reached
        assert ex.get("yn_c2", {}).get("matched_by") == "premap_names" or ex["step"] == "resolves"
        assert "certified" not in ex.get("yn_c5", {})


# ------------------------------------------------ 3. the Serie B league slot

# run414.log:117-122, verbatim (the venue's six srb-ver-are rows; event_title column)
VER_ARE_TITLE = "Hellas Verona FC vs. SS Arezzo"
Q_VER = "Will Hellas Verona FC win against SS Arezzo in the Serie B match scheduled for Sep 6, 2026?"
Q_ARE = "Will SS Arezzo win against Hellas Verona FC in the Serie B match scheduled for Sep 6, 2026?"
Q_VER_DRAW = "Will the Serie B match Hellas Verona FC vs SS Arezzo scheduled for Sep 6, 2026 end in a draw?"
Q_VIC = "Will L.R. Vicenza win against Virtus Entella in the Serie B match scheduled for Sep 6, 2026?"
VER_ARE = {f"srb-ver-are-{D}": (VER_ARE_TITLE, [
    _yn(f"atc-srb-ver-are-{D}-ver", Q_VER), _yn(f"atc-srb-ver-are-{D}-are", Q_ARE),
    _yn(f"atc-srb-ver-are-{D}-draw", Q_VER_DRAW)])}


class TestTheSerieBLeagueSlot:
    @pytest.mark.parametrize("lg", ["serie b", "serie a", "serie c", "k league 1", "ligue 1"])
    def test_the_letter_after_serie_is_the_tier(self, lg):
        assert pmus._yn_league_slot_bad(lg) is False

    @pytest.mark.parametrize("lg", ["primavera b", "serie b women", "serie b u21", "b", "b serie",
                                    "liga f", "serie a reserves", "a"])
    def test_every_other_scope_check_is_unchanged(self, lg):
        assert pmus._yn_league_slot_bad(lg) is True

    def test_the_verbatim_serie_b_rows_read(self):
        assert premap._yn_team_question(Q_VER, D) == (
            {"subj": "hellas verona fc", "opp": "ss arezzo", "lg": "serie b"}, None)
        assert premap._yn_draw_row({"question": Q_VER_DRAW}, d=D,
                                   sides=["hellas verona fc", "ss arezzo"]) is None
        # the club screen is not this rule: 'L.R. Vicenza' still refuses there
        assert pmus._yn_slot_bad("l r vicenza") is True
        assert premap._yn_team_question(Q_VIC, D) == (None, "yn:scope")

    def test_hellas_verona_maps_under_the_itsb_to_srb_alias(self, armed):
        rows = _board(VER_ARE)
        slug = f"itsb-ver-are-{D}-ver"
        h = _resolve(_Pool(rows), slug, "Will Hellas Verona FC win on 2026-09-06?", VER_ARE_TITLE, "Yes")
        assert _short(h) == (f"atc-srb-ver-are-{D}-ver", "yes", LONG, "premap_alias", "itsb->srb")
        h = _resolve(_Pool(rows), f"itsb-ver-are-{D}-draw", "Will Hellas Verona FC vs. SS Arezzo end in a draw?",
                     VER_ARE_TITLE, "Yes")
        assert _short(h) == (f"atc-srb-ver-are-{D}-draw", "yes", LONG, "premap_alias", "itsb->srb")
        # a women's twin of the same phrase still refuses at the slot
        bad = _with_question(rows, f"atc-srb-ver-are-{D}-ver", Q_VER.replace("Serie B", "Serie B Women"))
        ex = _explain(_Pool(bad), slug, "Will Hellas Verona FC win on 2026-09-06?", VER_ARE_TITLE, "Yes")
        assert ex["split"] == "yn:league-slot"


# ------------------------------------ 4. the empty prefix filter, named

# gap_soccer_venue_1346.log:334 (event_title, q_a, q_b verbatim; the draw in
# the venue's own dated draw template): the venue lists SIX atc rows and
# nothing else for Racing Club vs CA Tucuman
RAC_CAT_TITLE = "Racing Club vs. CA Tucuman"
RAC_CAT = {f"lpa-rac-cat-{D}": (RAC_CAT_TITLE, [
    _yn(f"atc-lpa-rac-cat-{D}-rac",
        "Will Racing Club win against CA Tucuman in the Liga Argentina match scheduled for Sep 6, 2026?"),
    _yn(f"atc-lpa-rac-cat-{D}-cat",
        "Will CA Tucuman win against Racing Club in the Liga Argentina match scheduled for Sep 6, 2026?"),
    _yn(f"atc-lpa-rac-cat-{D}-draw",
        "Will the Liga Argentina match Racing Club vs CA Tucuman scheduled for Sep 6, 2026 end in a draw?"),
])}
# prefix_rows_1347 / books-new_2249: atc-bra-bot-pal-2026-09-06-{bot,pal,draw}
# (identifiers verbatim; the questions in the Brasileirao P4 template)
BOT_PAL = {f"bra-bot-pal-{D}": ("Botafogo FR vs. SE Palmeiras", [
    _yn(f"atc-bra-bot-pal-{D}-bot",
        "Will Botafogo FR win against SE Palmeiras in the Brasileirao match scheduled for Sep 6, 2026?"),
    _yn(f"atc-bra-bot-pal-{D}-pal",
        "Will SE Palmeiras win against Botafogo FR in the Brasileirao match scheduled for Sep 6, 2026?"),
    _yn(f"atc-bra-bot-pal-{D}-draw",
        "Will the Brasileirao match Botafogo FR vs SE Palmeiras scheduled for Sep 6, 2026 end in a draw?"),
])}
# his rows, verbatim (prefix_rows_1347 / his-recent_0238:346)
HIS_RAC_TOTAL = (f"arg-rac-cat-{D}-total-2pt5", "Racing Club vs. CA Tucumán: O/U 2.5", None, "Under")
# the spread title names one club and builds no matchup key: his stored
# event title carries the keys (the derivative's '… - More Markets' shape,
# gap_soccer.md §1 -- read through item 1's head)
HIS_RAC_SPREAD = (f"arg-rac-cat-{D}-spread-away-1pt5", "Spread: Racing Club (-1.5)",
                  "Racing Club vs. CA Tucumán - More Markets", "CA Tucumán")
HIS_BOT_FH = (f"bra-bot-pal-{D}-first-half-total-0pt5", "Botafogo FR vs. SE Palmeiras: 1st Half O/U 0.5",
              None, "Under")
TSC_Q = "Will the total in RAC vs CAT be more than 2.5?"


class TestTheEmptyPrefixFilterIsNamedByTheBoard:
    def test_1_the_total_on_a_moneyline_only_league_is_kind_absent_league_unlisted(self, armed):
        p = _Pool(_board(RAC_CAT))
        ex = _explain(p, *HIS_RAC_TOTAL)
        assert ex["step"] == "type_prefix_filter_emptied" and ex["rows"] == 6
        assert ex["split"] == ex["refusal"] == "total:kind-absent:league-unlisted"
        assert p.asked == ["tsc-lpa-%"] and ex["kind_probe"]["league_codes"] == ["lpa"]
        assert _resolve(_Pool(_board(RAC_CAT)), *HIS_RAC_TOTAL) is None

    def test_1_dark_the_step_is_bare_and_resolve_is_none(self, dark):
        p = _Pool(_board(RAC_CAT))
        ex = _explain(p, *HIS_RAC_TOTAL)
        assert ex["step"] == "type_prefix_filter_emptied" and "split" not in ex
        assert p.asked == [] and _resolve(p, *HIS_RAC_TOTAL) is None

    def test_2_the_spread_asks_for_its_own_kind(self, armed):
        p = _Pool(_board(RAC_CAT))
        ex = _explain(p, *HIS_RAC_SPREAD)
        assert ex["split"] == "spread:kind-absent:league-unlisted" and p.asked == ["asc-lpa-%"]
        assert _resolve(p, *HIS_RAC_SPREAD) is None

    def test_3_a_first_half_total_is_kind_absent_not_segment_absent(self, armed):
        p = _Pool(_board(BOT_PAL))
        ex = _explain(p, *HIS_BOT_FH)
        assert ex["step"] == "type_prefix_filter_emptied"
        assert ex["split"] == "total:kind-absent:league-unlisted" and p.asked == ["tsc-bra-%"]
        assert _resolve(p, *HIS_BOT_FH) is None

    def test_4_a_league_the_venue_lists_the_kind_for_reads_league_listed(self, armed):
        # the eve-mnu P4 rows alone (the fast-lane case: the family exists under epl)
        atc = {k: (t, [m for m in mk if m["slug"].startswith("atc-") and "exact" not in m["slug"]])
               for k, (t, mk) in c5.VENUE.items()}
        p = _Pool(_board(atc), listed=("tsc-epl-%",))
        args = (f"epl-eve-mun-{D}-total-2pt5", "Everton FC vs. Manchester United FC: O/U 2.5", EVE_MNU, "Under")
        ex = _explain(p, *args)
        assert ex["step"] == "type_prefix_filter_emptied"
        assert ex["split"] == "total:kind-absent:league-listed" and ex["kind_probe"]["listed"] == "tsc-epl-%"
        assert _resolve(p, *args) is None

    def test_5_the_counterfactual_row_maps_and_the_segment_twin_stays_segment_absent(self, armed):
        full = {**RAC_CAT}
        full[f"lpa-rac-cat-{D}"] = (RAC_CAT_TITLE, RAC_CAT[f"lpa-rac-cat-{D}"][1]
                                    + [_tsc(f"tsc-lpa-rac-cat-{D}-2pt5", TSC_Q)])
        p = _Pool(_board(full))
        ex = _explain(p, *HIS_RAC_TOTAL)
        assert ex["step"] == "resolves" and ex["detail"] == f"tsc-lpa-rac-cat-{D}-2pt5"
        assert p.asked == []                       # the probe runs only on the refusal
        h = _resolve(p, *HIS_RAC_TOTAL)
        assert _short(h) == (f"tsc-lpa-rac-cat-{D}-2pt5", "under", SHORT, "premap", None)
        fh = {**RAC_CAT}
        fh[f"lpa-rac-cat-{D}"] = (RAC_CAT_TITLE, RAC_CAT[f"lpa-rac-cat-{D}"][1]
                                  + [_tsc(f"tsc-lpa-rac-cat-{D}-fh-2pt5", TSC_Q)])
        p = _Pool(_board(fh))
        ex = _explain(p, *HIS_RAC_TOTAL)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "total:segment-absent"
        assert p.asked == [] and _resolve(p, *HIS_RAC_TOTAL) is None

    def test_6_the_probe_never_widens_a_match_and_resolve_is_untouched(self):
        src = inspect.getsource(premap.resolve)
        assert "kind-absent" not in src and "_kind_absent_probe" not in src
        # the refusal returns before c3_same_segment / c3_pick can see the rows
        ex_src = inspect.getsource(premap.resolve_explain)
        i = ex_src.index("_kind_absent_probe(")
        assert ex_src.index("return out", i) < ex_src.index("c3_same_segment(", i)

    def test_7_explain_unmapped_renders_the_split(self, armed):
        p = _Pool(_board(RAC_CAT))
        ctx = {"title": HIS_RAC_TOTAL[1], "event_title": None, "outcome": "Under", "his_slug": HIS_RAC_TOTAL[0]}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "type_prefix_filter_emptied:total:kind-absent:league-unlisted"

    def test_a_failed_probe_names_the_family_and_the_error(self, armed):
        class _Broken(_Pool):
            async def fetch(self, sql, *a):
                if "LIKE" in sql:
                    raise RuntimeError("down")
                return await super().fetch(sql, *a)

        ex = _explain(_Broken(_board(RAC_CAT)), *HIS_RAC_TOTAL)
        assert ex["split"] == "total:kind-absent" and ex["kind_probe"]["error"] == "RuntimeError"


# ------------------------------------------- 5. spread sides unparsed

def _stale_asc(rows: list[dict]) -> list[dict]:
    """The eve-mnu asc rows as us_premap held them on 2026-09-06
    (gap_soccer.md §1, epl_rows_1338.log): ONE row per identifier,
    side_norm '1 50' / '2 50', BUY_SHORT -- the pre-C3 sweep's digit
    side on an ended game."""
    out, seen = [], set()
    for r in rows:
        if not r["identifier"].startswith(f"asc-epl-eve-mnu-{D}-"):
            out.append(r)
            continue
        if r["identifier"] in seen:
            continue
        seen.add(r["identifier"])
        out.append(dict(r, side_norm=f"{r['line'][0]} 50", intent=SHORT))
    return out


class TestSpreadSidesUnparsed:
    def test_the_listed_line_with_digit_sides_is_sides_unparsed(self, armed):
        rows = _stale_asc(c5._board())
        slug, title, ev, oc = c5.FEED["spread"]
        assert _resolve(_Pool(rows), slug, title, ev, oc) is None
        ex = _explain(_Pool(rows), slug, title, ev, oc)
        assert ex["step"] == "no_side_match" and ex["split"] == "spread:sides-unparsed"
        assert ex["yn_c5"]["certified"] is True
        assert ex["yn_c5"]["lane"]["wanted"] == f"asc-epl-eve-mnu-{D}-pos-1pt5"

    def test_the_same_rows_with_yes_no_sides_map(self, armed):
        slug, title, ev, oc = c5.FEED["spread"]
        h = _resolve(_Pool(c5._board()), slug, title, ev, oc)
        assert _short(h) == (f"asc-epl-eve-mnu-{D}-pos-1pt5", "no", SHORT, "premap_spread", None)

    def test_a_missing_line_stays_line_absent(self, armed):
        rows = [r for r in c5._board() if not r["identifier"].endswith("-pos-1pt5")]
        slug, title, ev, oc = c5.FEED["spread"]
        ex = _explain(_Pool(rows), slug, title, ev, oc)
        assert ex["split"] == "spread:line-absent"
        # and the digit rows on ANOTHER line never make a missing line 'unparsed'
        ex = _explain(_Pool(_stale_asc(rows)), slug, title, ev, oc)
        assert ex["split"] == "spread:line-absent"


# -------------------------------------------------- 6. the numbered club (P6)

# gap_soccer_venue_1346.log: event_title, q_a, q_b verbatim (:374 sea-bol-sas
# also in the C2 dump; :347 lg1-ang-ren; :381 sea-par-mon; :384 swsl-fcb-lug;
# :357 srb-mod-ave; :338 bun-hsv-mai); the draw / tsc questions in the
# venue's own templates
def _game(lg, va, vb, A, B, lgname, ev_title, *, totals=()):
    e = f"{lg}-{va}-{vb}-{D}"
    p4 = "Will {a} win against {b} in the {lg} match scheduled for Sep 6, 2026?"
    mk = [_yn(f"atc-{e}-{va}", p4.format(a=A, b=B, lg=lgname)),
          _yn(f"atc-{e}-{vb}", p4.format(a=B, b=A, lg=lgname)),
          _yn(f"atc-{e}-draw", f"Will the {lgname} match {A} vs {B} scheduled for Sep 6, 2026 end in a draw?")]
    for ln in totals:
        mk.append(_tsc(f"tsc-{e}-{ln}", f"Will the total in {va.upper()} vs {vb.upper()} be more than "
                                        f"{ln.replace('pt', '.')}?"))
    return {e: (ev_title, mk)}


NUMBERED = {
    **_game("sea", "bol", "sas", "Bologna FC 1909", "US Sassuolo Calcio", "Serie A",
            "Bologna FC 1909 vs. US Sassuolo Calcio"),
    **_game("lg1", "ang", "ren", "Angers SCO", "Stade Rennais FC 1901", "Ligue 1",
            "Angers SCO vs. Stade Rennais FC 1901"),
    **_game("sea", "par", "mon", "Parma Calcio 1913", "AC Monza", "Serie A", "Parma Calcio 1913 vs. AC Monza"),
    **_game("swsl", "fcb", "lug", "FC Basel 1893", "FC Lugano", "Swiss Super League", "FC Basel 1893 vs. FC Lugano"),
    **_game("srb", "mod", "ave", "Modena FC 2018", "US Avellino 1912", "Serie B", "Modena FC 2018 vs. US Avellino 1912"),
    **_game("bun", "hsv", "mai", "Hamburger SV", "1. FSV Mainz 05", "Bundesliga", "Hamburger SV vs. 1. FSV Mainz 05",
            totals=("0pt5", "1pt5", "2pt5", "3pt5")),
}
# his rows, verbatim (gap_soccer_his_1346.log:253,271,277,337,370,334,359,307,304)
HIS_NUMBERED = {
    "bol": (f"sea-bol-sas-{D}-bol", "Will Bologna FC 1909 win on 2026-09-06?",
            "Bologna FC 1909 vs. US Sassuolo Calcio", f"atc-sea-bol-sas-{D}-bol", "premap_identity", None),
    "ren": (f"fl1-ang-ren-{D}-ren", "Will Stade Rennais FC 1901 win on 2026-09-06?",
            "Angers SCO vs. Stade Rennais FC 1901", f"atc-lg1-ang-ren-{D}-ren", "premap_alias", "fl1->lg1"),
    "par": (f"sea-par-mon-{D}-par", "Will Parma Calcio 1913 win on 2026-09-06?",
            "Parma Calcio 1913 vs. AC Monza", f"atc-sea-par-mon-{D}-par", "premap_identity", None),
    "fcb": (f"sui-fcb-lug-{D}-fcb", "Will FC Basel 1893 win on 2026-09-06?",
            "FC Basel 1893 vs. FC Lugano", f"atc-swsl-fcb-lug-{D}-fcb", "premap_alias", "sui->swsl"),
    "ave": (f"itsb-mod-ave-{D}-ave", "Will US Avellino 1912 win on 2026-09-06?",
            "Modena FC 2018 vs. US Avellino 1912", f"atc-srb-mod-ave-{D}-ave", "premap_alias", "itsb->srb"),
    "mai": (f"bun-hsv-mai-{D}-mai", "Will 1. FSV Mainz 05 win on 2026-09-06?",
            "Hamburger SV vs. 1. FSV Mainz 05", f"atc-bun-hsv-mai-{D}-mai", "premap_identity", None),
}
BOL = f"atc-sea-bol-sas-{D}-bol"
Q_BOL = "Will Bologna FC 1909 win against US Sassuolo Calcio in the Serie A match scheduled for Sep 6, 2026?"


class TestTheNumberedClub:
    def test_the_c2_dump_carries_the_bologna_row_verbatim(self):
        assert (BOL, Q_BOL) in c2.VENUE[f"aec-sea-bol-sas-{D}"][1]
        assert NUMBERED[f"sea-bol-sas-{D}"][1][0]["question"] == Q_BOL

    @pytest.mark.parametrize("key", sorted(HIS_NUMBERED))
    def test_each_numbered_club_maps_when_the_venue_restates_the_number(self, armed, key):
        slug, title, ev, want_id, label, alias = HIS_NUMBERED[key]
        rows = _board(NUMBERED)
        for oc, side, intent in (("Yes", "yes", LONG), ("No", "no", SHORT)):
            h = _resolve(_Pool(rows), slug, title, ev, oc)
            assert _short(h) == (want_id, side, intent, label, alias), (key, oc)
        ex = _explain(_Pool(rows), slug, title, ev, "Yes")
        assert ex["step"] == "resolves" and ex["detail"] == want_id
        # the arm's record names the numbered subject it held the venue to
        tr: dict = {}
        premap._yn_pick(rows, "Yes", title, slug, ev, tr)
        assert tr["numbered"] == " ".join(pmus._norm_folded(title).split())[5:-len(" win on 2026 09 06")]
        assert tr["matched_by"] == label

    def test_the_bridge_digit_rule_is_untouched(self):
        assert premap._bridge_title_subject("Will FC Schalke 04 win on 2026-09-03?",
                                            "bl1-s04-bvb-2026-09-03-s04") == (None, "subject_has_digit")
        assert premap._yn_title_is_his_game("Will FC Schalke 04 win on 2026-09-03?",
                                            "bl1-s04-bvb-2026-09-03-s04") is False
        assert premap._bridge_title_subject_any("Will FC Schalke 04 win on 2026-09-03?",
                                                "bl1-s04-bvb-2026-09-03-s04") == ("fc schalke 04", "ok")
        # the wording arm reads the bridge's rule, never the raw subject
        assert "_bridge_title_subject_any" not in inspect.getsource(premap.match_side)
        assert "_bridge_title_subject_any" not in inspect.getsource(premap._yn_title_is_his_game)

    @pytest.mark.parametrize("venue_name", ["Bologna FC", "Bologna FC 1919", "Bologna FC 1909 Salerno"])
    def test_a_number_the_venue_does_not_restate_stays_name_digits(self, armed, venue_name):
        slug, title, ev = HIS_NUMBERED["bol"][:3]
        rows = _with_question(_board(NUMBERED), BOL, Q_BOL.replace("Bologna FC 1909", venue_name))
        assert _resolve(_Pool(rows), slug, title, ev, "Yes") is None
        ex = _explain(_Pool(rows), slug, title, ev, "Yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:name-digits"

    def test_raw_token_set_equality_no_generic_token_removal(self):
        assert pmus._yn_name_match_raw("bologna fc 1909", "bologna fc 1909")
        assert not pmus._yn_name_match_raw("bologna fc 1909", "bologna 1909")
        assert not pmus._yn_name_match_raw("bologna fc 1909", "bologna fc")
        assert not pmus._yn_name_match_raw("1 fsv mainz 05", "mainz 05")
        # _yn_name_match's distinctive branch would have matched these two
        assert pmus._yn_name_match("bologna fc 1909", "bologna 1909") is True

    def test_his_short_mainz_title_against_the_venues_full_name_refuses(self, armed):
        rows = _board(NUMBERED)
        args = (f"bun-hsv-mai-{D}-mai", "Will Mainz 05 win on 2026-09-06?", "Hamburger SV vs. 1. FSV Mainz 05", "Yes")
        assert _resolve(_Pool(rows), *args) is None
        assert _explain(_Pool(rows), *args)["split"] == "yn:name-digits"

    def test_a_lined_slug_never_enters(self, armed):
        tr: dict = {}
        out = premap._yn_pick(_board(NUMBERED), "Yes", "Will Bologna FC 1909 win on 2026-09-06?",
                              f"sea-bol-sas-{D}-total-2pt5", "Bologna FC 1909 vs. US Sassuolo Calcio", tr)
        assert out == [] and tr["refusal"] == "yn:name-digits" and "numbered" not in tr

    def test_the_c2_pin_that_reversed(self, armed):
        """test_c2_yesno_alias.py's 'Will Mainz 05 win …' pin encoded the
        bridge's caution; the venue's row there restates 'Mainz 05'
        verbatim, so it maps now -- by design."""
        q = ("Will Mainz 05 win against Eintracht Frankfurt in the Bundesliga match scheduled "
             "for Sep 6, 2026?")
        venue = {f"aec-bun-mai-fra-{D}": ("Mainz 05 vs. Eintracht Frankfurt", [(f"atc-bun-mai-fra-{D}-mai", q)])}
        h = c2._resolve(c2._board(venue), f"bun-mai-fra-{D}-mai", "Will Mainz 05 win on 2026-09-06?",
                        "Mainz 05 vs. Eintracht Frankfurt", "Yes")
        assert c2._short(h) == (f"atc-bun-mai-fra-{D}-mai", LONG, "premap_identity", None)


# ------------------------------------------- 7. the club-slot screen (P7)

Q_MAI = "Will 1. FSV Mainz 05 win against Hamburger SV in the Bundesliga match scheduled for Sep 6, 2026?"


class TestTheClubSlotSingleCharacterRuleIsLettersOnly:
    def test_the_lone_digit_is_not_a_scope_letter(self):
        assert pmus._yn_slot_bad("1 fsv mainz 05") is False
        assert premap._yn_team_question(Q_MAI, D) == (
            {"subj": "1 fsv mainz 05", "opp": "hamburger sv", "lg": "bundesliga"}, None)
        assert premap._yn_event_sides("Hamburger SV vs. 1. FSV Mainz 05") == ["hamburger sv", "1 fsv mainz 05"]
        assert premap._c3_title_parts("Hamburger SV vs. 1. FSV Mainz 05: O/U 4.5")[0] == ["hamburger sv", "1 fsv mainz 05"]

    @pytest.mark.parametrize("slot", ["b", "u", "l r vicenza", "sc braga b", "", "one two three four five six",
                                      "juventus u21", "shoot out", "s o"])
    def test_every_letter_and_scope_check_is_unchanged(self, slot):
        assert pmus._yn_slot_bad(slot) is True

    def test_the_bridges_own_rule_is_byte_identical(self):
        assert premap._has_scope_token("1 fsv mainz 05") is True      # the wording arm keeps its rule
        src = inspect.getsource(premap._has_scope_token)
        assert 'len(t) == 1 and t not in ("y", "e")' in src

    def test_the_hamburg_mainz_rows(self, armed):
        rows = _board(NUMBERED)
        ev = "Hamburger SV vs. 1. FSV Mainz 05"
        h = _resolve(_Pool(rows), f"bun-hsv-mai-{D}-hsv", "Will Hamburger SV win on 2026-09-06?", ev, "Yes")
        assert _short(h) == (f"atc-bun-hsv-mai-{D}-hsv", "yes", LONG, "premap_identity", None)
        h = _resolve(_Pool(rows), f"bun-hsv-mai-{D}-draw", "Will Hamburger SV vs. 1. FSV Mainz 05 end in a draw?",
                     ev, "No")
        assert _short(h) == (f"atc-bun-hsv-mai-{D}-draw", "no", SHORT, "premap_identity", None)
        # the total at 4.5: the venue lists 0.5-3.5 -- the true refusal, not title-shape
        args = (f"bun-hsv-mai-{D}-total-4pt5", "Hamburger SV vs. 1. FSV Mainz 05: O/U 4.5", f"{ev} - More Markets", "Over")
        assert _resolve(_Pool(rows), *args) is None
        assert _explain(_Pool(rows), *args)["split"] == "total:line-absent"
        h = _resolve(_Pool(rows), f"bun-hsv-mai-{D}-total-3pt5", "Hamburger SV vs. 1. FSV Mainz 05: O/U 3.5",
                     f"{ev} - More Markets", "Over")
        assert _short(h) == (f"tsc-bun-hsv-mai-{D}-3pt5", "over", LONG, "premap", None)
