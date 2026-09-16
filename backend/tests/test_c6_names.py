"""C6-N (2026-09-07): the names-witnessed per-team row when NO code is
shared -- his league code AND both club codes differ from the venue's,
and the venue's own per-team question naming his club (as his title
states it) against his opponent (as his event title states it), on his
date, is the only witness. Never a table, never a code similarity.

The venue's rows for his Primera Chile (`atc-pdc-…`) and Liga Colombia
(`atc-lco-…`) games of 2026-09-06 are reproduced from the esports-chi-rows
read (13:52Z, gap_esports_chi.md §1.1-1.2) verbatim where the file
prints them; the sibling rows the file names by identifier and template
only carry the same wording as an EXPECTATION about the lookup, never a
decision (every decision below reads the question). His feed rows are
the file's, verbatim.

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from sportsassets import copy_sports, pmus
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"

P4 = "Will {a} win against {b} in the {lg} match scheduled for Sep 6, 2026?"
DRAWQ = "Will the {lg} match {a} vs {b} scheduled for Sep 6, 2026 end in a draw?"


def _yn(ident: str, q: str) -> dict:
    """The venue's own side expansion: both sides share the identifier;
    `long` names the intent (the read's intents: yes=LONG, no=SHORT)."""
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _game(lg: str, phrase: str, va: str, vb: str, A: str, B: str) -> list[dict]:
    e = f"{lg}-{va}-{vb}-{D}"
    return [_yn(f"atc-{e}-{va}", P4.format(a=A, b=B, lg=phrase)),
            _yn(f"atc-{e}-{vb}", P4.format(a=B, b=A, lg=phrase)),
            _yn(f"atc-{e}-draw", DRAWQ.format(a=A, b=B, lg=phrase))]


# the venue, by event slug -> (event title, markets). hua-col's three rows
# and the -uco / -nub / -mil / -per / -leq questions are the file's
# verbatim text; the rest follow the template the read attests.
VENUE = {
    f"pdc-hua-col-{D}": ("CD Huachipato vs. CSD Colo-Colo", [
        _yn(f"atc-pdc-hua-col-{D}-col",
            "Will CSD Colo-Colo win against CD Huachipato in the Primera Chile match scheduled "
            "for Sep 6, 2026?"),
        _yn(f"atc-pdc-hua-col-{D}-hua",
            "Will CD Huachipato win against CSD Colo-Colo in the Primera Chile match scheduled "
            "for Sep 6, 2026?"),
        _yn(f"atc-pdc-hua-col-{D}-draw",
            "Will the Primera Chile match CD Huachipato vs CSD Colo-Colo scheduled for Sep 6, "
            "2026 end in a draw?")]),
    f"pdc-pal-uco-{D}": ("CD Palestino vs. CD Universidad de Concepcion",
                         _game("pdc", "Primera Chile", "pal", "uco", "CD Palestino",
                               "CD Universidad de Concepcion")),
    f"pdc-ser-nub-{D}": ("CD La Serena vs. CD Nublense",
                         _game("pdc", "Primera Chile", "ser", "nub", "CD La Serena", "CD Nublense")),
    f"pdc-ohi-lac-{D}": ("O'Higgins FC vs. CD Union La Calera",
                         _game("pdc", "Primera Chile", "ohi", "lac", "O'Higgins FC",
                               "CD Union La Calera")),
    f"lco-per-mil-{D}": ("Deportivo Pereira vs. Millonarios FC",
                         _game("lco", "Liga Colombia", "per", "mil", "Deportivo Pereira",
                               "Millonarios FC")),
    f"lco-leq-agu-{D}": ("Internacional de Bogota vs. Aguilas Doradas Rionegro",
                         _game("lco", "Liga Colombia", "leq", "agu", "Internacional de Bogota",
                               "Aguilas Doradas Rionegro")),
}

# his feed: market_slug, market_title, markets.event_title, outcomes traded
HUA_COL = "CD Huachipato vs. CSD Colo-Colo"
FEED = {
    "csc": (f"chi1-cdh-csc-{D}-csc", "Will CSD Colo-Colo win on 2026-09-06?", HUA_COL),
    "cdh": (f"chi1-cdh-csc-{D}-cdh", "Will CD Huachipato win on 2026-09-06?", HUA_COL),
    "hc-draw": (f"chi1-cdh-csc-{D}-draw", "Will CD Huachipato vs. CSD Colo-Colo end in a draw?", HUA_COL),
    "cdp": (f"chi1-cdp-cu1-{D}-cdp", "Will CD Palestino win on 2026-09-06?",
            "CD Palestino vs. CD Universidad de Concepción"),
    "cu1": (f"chi1-cdp-cu1-{D}-cu1", "Will CD Universidad de Concepción win on 2026-09-06?",
            "CD Palestino vs. CD Universidad de Concepción"),
    "cls": (f"chi1-cls-cdu-{D}-cls", "Will CD La Serena win on 2026-09-06?", "CD La Serena vs. CD Ñublense"),
    "ohf": (f"chi1-ohf-cul-{D}-ohf", "Will O'Higgins FC win on 2026-09-06?",
            "O'Higgins FC vs. CD Unión La Calera"),
    "mif": (f"col1-dep-mif-{D}-mif", "Will Millonarios FC win on 2026-09-06?", None),
    "dep": (f"col1-dep-mif-{D}-dep", "Will Deportivo Pereira win on 2026-09-06?",
            "Deportivo Pereira vs. Millonarios FC"),
    "dm-draw": (f"col1-dep-mif-{D}-draw", "Will Deportivo Pereira vs. Millonarios FC end in a draw?",
                "Deportivo Pereira vs. Millonarios FC"),
    "cle": (f"col1-cle-gdr-{D}-cle", "Will Internacional de Bogotá win on 2026-09-06?",
            "Internacional de Bogotá vs. Águilas Doradas Rionegro"),
    "gdr": (f"col1-cle-gdr-{D}-gdr", "Will Águilas Doradas Rionegro win on 2026-09-06?",
            "Internacional de Bogotá vs. Águilas Doradas Rionegro"),
    "cg-draw": (f"col1-cle-gdr-{D}-draw",
                "Will Internacional de Bogotá vs. Águilas Doradas Rionegro end in a draw?", None),
}
DEP_TITLE = FEED["dep"][1]
HIS_ML = {f"col1-dep-mif-{D}-dep": [DEP_TITLE]}

COL, HUA, HC_DRAW = f"atc-pdc-hua-col-{D}-col", f"atc-pdc-hua-col-{D}-hua", f"atc-pdc-hua-col-{D}-draw"
MIL, PER, DM_DRAW = f"atc-lco-per-mil-{D}-mil", f"atc-lco-per-mil-{D}-per", f"atc-lco-per-mil-{D}-draw"
LEQ, AGU, CG_DRAW = f"atc-lco-leq-agu-{D}-leq", f"atc-lco-leq-agu-{D}-agu", f"atc-lco-leq-agu-{D}-draw"
UNWIT, SHEAR, AMBIG, MISMATCH = ("yn:names-unwitnessed", "yn:names-shear", "yn:names-ambiguous",
                                 "yn:names-mismatch")


def _board(venue: dict | None = None, extra: dict | None = None, *, sweep_keys: bool = True) -> list[dict]:
    """Rows keyed as the sweep keys them: the event's keys plus each
    row's own name keys (premap.keys_for_row). `sweep_keys=False` keys
    the event alone, the pre-C6 sweep."""
    rows: list[dict] = []
    for src in (venue if venue is not None else VENUE, extra or {}):
        for ev_slug, (ev_title, mkts) in src.items():
            keys = premap.event_keys_for(ev_title, ev_slug)
            for m in mkts:
                for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m):
                    r["event_keys"] = premap.keys_for_row(keys, r) if sweep_keys else list(keys)
                    rows.append(r)
    return rows


class _Pool:
    """us_premap by key intersection (and the census probe's identifier
    pattern); trades/markets titles by slug (the C5 read C6-N shares)."""

    def __init__(self, rows, titles: dict[str, list[str]] | None = None):
        self.rows = rows
        self.titles = titles or {}
        self.reads: list[str] = []

    async def fetch(self, sql, *a):
        if "us_premap" in sql:
            self.reads.append("us_premap")
            if "identifier ~" in sql:
                return [dict(r) for r in self.rows if re.search(a[0], r["identifier"])]
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        assert "trades" in sql and "markets" in sql, sql
        assert a[1] == a[0].rsplit("-", 1)[0], a
        self.reads.append("titles")
        return [{"t": t} for t in self.titles.get(a[0], [])]


def _resolve(rows, key, oc, *, ev="keep", titles=None, pool=None):
    slug, title, evt = FEED[key]
    p = pool or _Pool(rows, titles)
    return asyncio.run(premap.resolve(p, title, evt if ev == "keep" else ev, oc, slug))


def _explain(rows, key, oc, *, ev="keep", titles=None, pool=None):
    slug, title, evt = FEED[key]
    p = pool or _Pool(rows, titles)
    return asyncio.run(premap.resolve_explain(p, title, evt if ev == "keep" else ev, oc, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


def _pick(rows, key, oc, *, ev="keep", other=None):
    slug, title, evt = FEED[key]
    tr: dict = {}
    out = premap._yn_pick(rows, oc, title, slug, evt if ev == "keep" else ev, tr, names_witness=other)
    return out, tr


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


@pytest.fixture
def cu1_is_a_moneyline(monkeypatch):
    """M1's parser fix (a team code with a digit is the slug's own side
    token) is another builder's; until it lands the cu1 slug is typed
    here so the arm is pinned on the verbatim rows."""
    real = copy_sports.market_type_of
    monkeypatch.setattr(copy_sports, "market_type_of",
                        lambda s: "moneyline" if s == FEED["cu1"][0] else real(s))


def _with_question(rows, ident, q):
    return [dict(r, question=q) if r["identifier"] == ident else dict(r) for r in rows]


# ------------------------------------------- (a) (b) (c): Colo-Colo's game

class TestTheVerbatimChileRows:
    def test_a_his_no_and_yes_on_colo_colo_land_on_the_venues_col_row(self, armed):
        rows = _board()
        assert _short(_resolve(rows, "csc", "No")) == (COL, "no", SHORT, "premap_names")
        h = _resolve(rows, "csc", "Yes")
        assert _short(h) == (COL, "yes", LONG, "premap_names")
        assert h["code_pair"] == {"cdh": "hua", "csc": "col"} and h["witness"] == COL
        assert h["league_alias"] == "chi1->pdc" and "code_translated" not in h
        ex = _explain(rows, "csc", "Yes")
        assert ex["step"] == "resolves" and ex["detail"] == COL
        assert ex["matched_by"] == "premap_names" and ex["code_pair"] == {"cdh": "hua", "csc": "col"}
        assert ex["witness"] == COL and ex["league_alias"] == "chi1->pdc"

    def test_b_the_other_club_takes_the_other_contract(self, armed):
        rows = _board()
        assert _short(_resolve(rows, "cdh", "Yes")) == (HUA, "yes", LONG, "premap_names")
        assert _short(_resolve(rows, "cdh", "No")) == (HUA, "no", SHORT, "premap_names")

    def test_c_the_draw_of_the_witnessed_event(self, armed):
        rows = _board()
        y, n = _resolve(rows, "hc-draw", "Yes"), _resolve(rows, "hc-draw", "No")
        assert _short(y) == (HC_DRAW, "yes", LONG, "premap_names")
        assert _short(n) == (HC_DRAW, "no", SHORT, "premap_names")
        assert y["code_pair"] == {"cdh": "hua", "csc": "col"} and y["witness"] in (COL, HUA)
        # no event title: his draw title names both sides (C3's witness)
        h = _resolve(rows, "hc-draw", "Yes", ev=None)
        assert _short(h) == (HC_DRAW, "yes", LONG, "premap_names")
        assert _pick(rows, "hc-draw", "Yes", ev=None)[1]["draw_witness"] == "title"
        assert _pick(rows, "hc-draw", "Yes")[1]["draw_witness"] == "event"

    def test_d_a_team_code_with_a_digit_once_typed_as_a_moneyline(self, armed, cu1_is_a_moneyline):
        rows = _board()
        h = _resolve(rows, "cu1", "Yes")
        assert _short(h) == (f"atc-pdc-pal-uco-{D}-uco", "yes", LONG, "premap_names")
        assert h["code_pair"] == {"cdp": "pal", "cu1": "uco"}
        assert _short(_resolve(rows, "cdp", "No")) == (f"atc-pdc-pal-uco-{D}-pal", "no", SHORT, "premap_names")

    def test_the_other_chile_games_read_the_same_way(self, armed):
        rows = _board()
        assert _resolve(rows, "cls", "No")["market_slug"] == f"atc-pdc-ser-nub-{D}-ser"
        assert _resolve(rows, "ohf", "No")["market_slug"] == f"atc-pdc-ohi-lac-{D}-ohi"


# ------------------------------------------- (e) (f): Colombia, the witness

class TestTheColombiaRowsAndTheStoredTitleWitness:
    def test_e_millonarios_with_the_event_title(self, armed):
        rows = _board()
        h = _resolve(rows, "mif", "Yes", ev="Deportivo Pereira vs. Millonarios FC")
        assert _short(h) == (MIL, "yes", LONG, "premap_names")
        assert h["code_pair"] == {"dep": "per", "mif": "mil"} and h["witness"] == MIL

    def test_e_millonarios_with_no_event_title_and_his_dep_title_in_the_pool(self, armed):
        """markets.event_title is NULL on this market ($24,576, the
        no_key_intersection row): his own name key fetches the venue's
        -mil row, and his stored moneyline title on the OTHER code
        ('Will Deportivo Pereira win on 2026-09-06?', trades) names the
        opponent -- the C5 read, bounded."""
        rows = _board()
        pool = _Pool(rows, HIS_ML)
        h = _resolve(rows, "mif", "No", pool=pool)
        assert _short(h) == (MIL, "no", SHORT, "premap_names")
        assert h["code_pair"] == {"dep": "per", "mif": "mil"} and h["witness"] == MIL
        assert pool.reads == ["us_premap", "titles"]
        ex = _explain(rows, "mif", "Yes", titles=HIS_ML)
        assert ex["step"] == "resolves" and ex["yn_c6"]["witness_src"] == "trades"
        assert ex["yn_c6"]["other"] == "deportivo pereira"
        assert _pick(rows, "mif", "Yes")[1]["refusal"] == UNWIT      # the pure arm alone

    def test_e_with_neither_witness_it_is_unwitnessed_by_name(self, armed):
        rows = _board()
        pool = _Pool(rows)
        assert _resolve(rows, "mif", "Yes", pool=pool) is None
        assert pool.reads == ["us_premap", "titles"]
        ex = _explain(rows, "mif", "Yes")
        assert (ex["step"], ex["split"]) == ("no_side_match", UNWIT)
        assert ex["yn_c6"] == {"refusal": UNWIT, "why": "no-stored-title"}

    def test_the_stored_titles_witness_nothing_when_they_disagree_or_overflow(self, armed):
        rows = _board()
        two = {f"col1-dep-mif-{D}-dep": [DEP_TITLE, "Will Deportivo Cali win on 2026-09-06?"]}
        assert _resolve(rows, "mif", "Yes", titles=two) is None
        assert _explain(rows, "mif", "Yes", titles=two)["yn_c6"]["why"] == "titles-disagree"
        nine = {f"col1-dep-mif-{D}-dep": [DEP_TITLE] * 9}
        assert _resolve(rows, "mif", "Yes", titles=nine) is None
        assert _explain(rows, "mif", "Yes", titles=nine)["yn_c6"]["why"] == "titles-overflow"
        # a title that is not his dated win question on that slug is no witness
        wrong = {f"col1-dep-mif-{D}-dep": ["Will Deportivo Pereira win on 2026-09-07?"]}
        assert _explain(rows, "mif", "Yes", titles=wrong)["yn_c6"]["why"] == "no-stored-title"

    def test_the_stored_title_is_read_only_when_the_pure_arm_said_unwitnessed(self, armed):
        rows = _board()
        pool = _Pool(rows, HIS_ML)
        _resolve(rows, "dep", "Yes", pool=pool)
        assert pool.reads == ["us_premap"]          # the event title witnessed it
        pool = _Pool(rows, HIS_ML)
        _resolve(rows, "csc", "Yes", ev="CSD Colo-Colo vs. CD Huachipato", pool=pool)
        assert pool.reads == ["us_premap"]          # shear: nothing to read

    def test_f_aguilas_doradas_folds_to_the_venues_ascii(self, armed):
        rows = _board()
        assert _short(_resolve(rows, "gdr", "No")) == (AGU, "no", SHORT, "premap_names")
        assert _short(_resolve(rows, "cle", "Yes")) == (LEQ, "yes", LONG, "premap_names")
        h = _resolve(rows, "cg-draw", "No")           # NULL event title: the draw title
        assert _short(h) == (CG_DRAW, "no", SHORT, "premap_names")
        assert h["code_pair"] == {"cle": "leq", "gdr": "agu"}
        assert _short(_resolve(rows, "dm-draw", "Yes")) == (DM_DRAW, "yes", LONG, "premap_names")


# ------------------------------------------- the lookup: the name key

class TestTheNameKey:
    def test_the_venue_rows_carry_the_subject_their_question_names(self):
        rows = _board()
        by = {(r["identifier"], r["side_norm"]): r for r in rows}
        assert f"csd colo colo@{D}" in by[(COL, "yes")]["event_keys"]
        assert f"cd huachipato@{D}" not in by[(COL, "yes")]["event_keys"]
        assert {f"cd huachipato@{D}", f"csd colo colo@{D}"} <= set(by[(HC_DRAW, "no")]["event_keys"])
        assert premap.venue_name_keys("Will CSD Colo-Colo lead CD Huachipato at halftime?",
                                      f"atc-pdc-hua-col-{D}-fh-col") == []
        assert premap.venue_name_keys(VENUE[f"pdc-hua-col-{D}"][1][0]["question"],
                                      "atc-pdc-hua-col-2026-09-07-col") == []   # dates disagree

    def test_his_side_keys_his_anchor_and_his_event_titles_sides(self):
        assert premap.his_name_keys(*FEED["csc"][1:], FEED["csc"][0]) == \
            [f"cd huachipato@{D}", f"csd colo colo@{D}"]
        assert premap.his_name_keys(FEED["mif"][1], None, FEED["mif"][0]) == [f"millonarios fc@{D}"]
        assert premap.his_name_keys(FEED["cg-draw"][1], None, FEED["cg-draw"][0]) == \
            [f"aguilas doradas rionegro@{D}", f"internacional de bogota@{D}"]
        assert premap.his_name_keys("Spread: Millonarios FC (-1.5)", None, f"col1-dep-mif-{D}-spread-away-1pt5") == []

    def test_the_venues_event_title_spelled_otherwise_still_meets_on_the_names(self, armed):
        """The venue's event title names the codes, not the clubs: no
        matchup key and no surname key meets his, and the pair key
        cannot (both codes differ) -- the name keys alone fetch the
        rows."""
        venue = dict(VENUE)
        venue[f"pdc-hua-col-{D}"] = ("CDH vs. CSC", VENUE[f"pdc-hua-col-{D}"][1])
        rows = _board(venue)
        assert _short(_resolve(rows, "csc", "No")) == (COL, "no", SHORT, "premap_names")
        assert _short(_resolve(rows, "hc-draw", "Yes")) == (HC_DRAW, "yes", LONG, "premap_names")
        ex = _explain(_board(venue, sweep_keys=False), "csc", "No")
        assert ex["step"] == "no_key_intersection" and ex["rows"] == 0

    def test_the_sweep_writes_the_row_keys(self):
        src = inspect.getsource(premap.refresh)
        assert src.count("_upsert(pool, r, keys_for_row(keys, r))") == 2
        assert "_upsert(pool, r, keys)" not in src


# ------------------------------------------- (g) (h): every refusal, by name

class TestEveryRefusalIsNamed:
    def test_g_a_title_naming_the_other_team_is_shear(self, armed):
        rows = _board()
        slug = FEED["csc"][0]
        assert asyncio.run(premap.resolve(_Pool(rows), FEED["cdh"][1], HUA_COL, "Yes", slug)) is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), FEED["cdh"][1], HUA_COL, "Yes", slug))
        assert ex["split"] == SHEAR

    def test_g_a_reversed_event_title_is_shear(self, armed):
        rows = _board()
        rev = "CSD Colo-Colo vs. CD Huachipato"
        assert _resolve(rows, "csc", "Yes", ev=rev) is None
        assert _explain(rows, "csc", "Yes", ev=rev)["split"] == SHEAR
        assert _resolve(rows, "hc-draw", "Yes", ev=rev) is None
        assert _explain(rows, "hc-draw", "Yes", ev=rev)["split"] == SHEAR

    def test_g_a_wrong_date_finds_no_row(self, armed):
        rows = _board()
        slug7 = "chi1-cdh-csc-2026-09-07-csc"
        t7 = "Will CSD Colo-Colo win on 2026-09-07?"
        assert asyncio.run(premap.resolve(_Pool(rows), t7, HUA_COL, "Yes", slug7)) is None
        assert asyncio.run(premap.resolve_explain(_Pool(rows), t7, HUA_COL, "Yes",
                                                  slug7))["step"] == "no_key_intersection"
        tr: dict = {}
        assert premap._yn_pick(rows, "Yes", t7, slug7, HUA_COL, tr) == []
        assert tr["refusal"] == "yn:no-row"

    def test_g_a_same_day_twin_under_another_code_is_ambiguous(self, armed):
        """A second listing on his date under `pdcx`, the same league
        phrase, the same two clubs (the coordinator's brb shape): the
        arm cannot say which is his and refuses -- for the moneyline
        and the draw alike."""
        twin = {f"pdcx-hua-col-{D}": ("CD Huachipato vs. CSD Colo-Colo",
                                      _game("pdcx", "Primera Chile", "hua", "col", "CD Huachipato",
                                            "CSD Colo-Colo"))}
        rows = _board(extra=twin)
        for key in ("csc", "cdh", "hc-draw"):
            assert _resolve(rows, key, "Yes") is None, key
            ex = _explain(rows, key, "Yes")
            assert ex["split"] == AMBIG, key
        assert _explain(rows, "csc", "Yes")["yn_c2"]["candidates"] == [COL, f"atc-pdcx-hua-col-{D}-col"]
        ctx = {"title": FEED["csc"][1], "event_title": HUA_COL, "outcome": "Yes", "his_slug": FEED["csc"][0]}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == f"no_side_match:{AMBIG}"
        # a women's twin is screened at the league slot and is no candidate
        w = {f"pdcw-hua-col-{D}": ("CD Huachipato vs. CSD Colo-Colo",
                                   _game("pdcw", "Primera Chile Femenina", "hua", "col",
                                         "CD Huachipato", "CSD Colo-Colo"))}
        assert _short(_resolve(_board(extra=w), "csc", "Yes")) == (COL, "yes", LONG, "premap_names")

    def test_g_the_venues_pair_naming_another_club_is_mismatch(self, armed):
        """'Gil Vicente Barcelos' is not 'Gil Vicente FC' under token-set
        equality -- refused by design. The venue's ligpor codes are
        unread (gap_league_codes §d): the file says no identifier ends
        with any por rest, so both codes differ here (an assumption about
        the lookup only); the questions are the census's verbatim
        wording. His opponent's name key fetches the -adv row, and the
        arm reads the pair."""
        venue = {f"ligpor-gvb-adv-{D}": ("Gil Vicente Barcelos vs. Academico de Viseu FC", [
            _yn(f"atc-ligpor-gvb-adv-{D}-gvb",
                "Will Gil Vicente Barcelos win against Academico de Viseu FC in the Liga Portugal "
                "match scheduled for Sep 6, 2026?"),
            _yn(f"atc-ligpor-gvb-adv-{D}-adv",
                "Will Academico de Viseu FC win against Gil Vicente Barcelos in the Liga Portugal "
                "match scheduled for Sep 6, 2026?")])}
        rows = _board(venue)
        slug, t = f"por-gil-acv-{D}-gil", "Will Gil Vicente FC win on 2026-09-06?"
        ev = "Gil Vicente FC vs. Académico de Viseu FC"
        tr: dict = {}
        assert premap._yn_pick(rows, "Yes", t, slug, ev, tr) == []
        assert tr["refusal"] == MISMATCH and tr["venue_rows"] == [f"atc-ligpor-gvb-adv-{D}-adv",
                                                                  f"atc-ligpor-gvb-adv-{D}-gvb"]
        assert asyncio.run(premap.resolve(_Pool(rows), t, ev, "Yes", slug)) is None
        # the lookup fetches the -adv row alone (his opponent's name key;
        # 'gil vicente barcelos' is not his key) and that row names the
        # pair in mirror: the same refusal, from the row the venue gave
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), t, ev, "Yes", slug))
        assert (ex["step"], ex["split"], ex["rows"]) == ("no_side_match", MISMATCH, 2)
        assert ex["yn_c2"]["venue_rows"] == [f"atc-ligpor-gvb-adv-{D}-adv"]
        # the venue sharing his `acv` code is C5's shape: the names arm
        # stands aside and C5 names the same mismatch its own way
        shared = {f"ligpor-gvb-acv-{D}": (venue[f"ligpor-gvb-adv-{D}"][0], [
            _yn(m["slug"].replace("-adv", "-acv"), m["question"])
            for m in venue[f"ligpor-gvb-adv-{D}"][1]])}
        rows = _board(shared)
        tr = {}
        premap._yn_pick(rows, "Yes", t, slug, ev, tr)
        assert tr["refusal"] == "yn:no-row"
        # his opponent's name key fetches the -acv row alone: C5 sees a
        # lone per-team row, witnesses nothing, today's reason stands
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), t, ev, "Yes", slug))
        assert ex["split"] == "yn:no-row" and "yn_c5" not in ex
        # with the event titles meeting (both rows fetched) C5 names its own
        shared[f"ligpor-gvb-acv-{D}"] = (ev, shared[f"ligpor-gvb-acv-{D}"][1])
        ex = asyncio.run(premap.resolve_explain(_Pool(_board(shared)), t, ev, "Yes", slug))
        assert ex["split"] == "yn:code-translate:name-mismatch"
        # and the mirror image: his club named, his opponent called otherwise
        rows2 = _with_question(_board(), COL, P4.format(a="CSD Colo-Colo", b="Universidad Catolica",
                                                         lg="Primera Chile"))
        assert _resolve(rows2, "csc", "Yes") is None
        assert _explain(rows2, "csc", "Yes")["split"] == MISMATCH
        # a partial name is not the club: 'Universidad Catolica' against
        # his 'Universidad Católica de Chile'; 'Junior FC' against his
        # 'CDP Junior FC' -- token-set equality, no containment
        for venue_name, his_name in (("Universidad Catolica", "Universidad Católica de Chile"),
                                     ("Junior FC", "CDP Junior FC")):
            rows3 = _with_question(_board(), HUA, P4.format(a=venue_name, b="CSD Colo-Colo",
                                                             lg="Primera Chile"))
            t, ev = f"Will {his_name} win on 2026-09-06?", f"{his_name} vs. CSD Colo-Colo"
            assert asyncio.run(premap.resolve(_Pool(rows3), t, ev, "Yes",
                                              f"chi1-cdh-csc-{D}-cdh")) is None, his_name
            ex = asyncio.run(premap.resolve_explain(_Pool(rows3), t, ev, "Yes", f"chi1-cdh-csc-{D}-cdh"))
            assert ex["split"] == MISMATCH, his_name

    def test_g_unwitnessed_when_his_event_title_names_no_two_sides(self, armed):
        rows = _board()
        for ev in (None, "", "CSD Colo-Colo", "A vs B vs C"):
            _out, tr = _pick(rows, "csc", "Yes", ev=ev)
            assert tr["refusal"] == UNWIT, ev
        _out, tr = _pick(rows, "csc", "Yes", ev="CD Huachipato vs. CSD Colo-Colo vs. X")
        assert tr["refusal"] == UNWIT
        _out, tr = _pick(rows, "csc", "Yes", ev="CD Palestino vs. CD Huachipato")
        assert tr["refusal"] == "yn:event-shear"

    def test_h_a_lined_venue_row_refuses_at_the_line_guard(self, armed):
        rows = _board()
        lined = [dict(r, line="1.5") if r["identifier"] == COL else dict(r) for r in rows]
        assert _resolve(lined, "csc", "Yes") is None
        assert _explain(lined, "csc", "Yes")["split"] == "yn:picked-vetoed"
        swapped = [dict(r, intent=SHORT if r["intent"] == LONG else LONG) for r in rows]
        assert _explain(swapped, "csc", "Yes")["split"] == "yn:intent"
        terse = _with_question(rows, COL, "Will CSD Colo-Colo win?")
        assert _explain(terse, "csc", "Yes")["split"] == "yn:no-row"
        later = _with_question(rows, COL, VENUE[f"pdc-hua-col-{D}"][1][0]["question"].replace("Sep 6", "Sep 7"))
        assert _explain(later, "csc", "Yes")["split"] == "yn:no-row"

    def test_the_refusal_names_are_closed_and_the_pure_functions_stay_pure(self):
        src = inspect.getsource(premap)
        names = {m.group(1) for m in re.finditer(r'"(yn:names-[a-z-]+)"', src)}
        assert names == {UNWIT, SHEAR, AMBIG, MISMATCH}
        for fn in (premap._yn_names_pick, premap._yn_names_draw_pick, premap._yn_pick):
            s = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv"):
                assert forbidden not in s, (fn.__name__, forbidden)
        # no static table of codes or clubs
        s = inspect.getsource(premap._yn_names_pick) + inspect.getsource(premap._yn_names_draw_pick)
        for lit in ("'pdc'", '"pdc"', "'lco'", "'hua'", "'col'", "colo"):
            assert lit not in s


# ------------------------------------------- (i): the earlier arms run first

class TestTheEarlierArmsRunFirstAndAreUntouched:
    def test_an_event_sharing_one_code_is_c5s_and_the_names_arm_stands_aside(self, armed):
        """The venue codes Huachipato `cdh` as his feed does: one shared
        code in position is C5's shape -- the names arm refuses yn:no-row
        and C5 certifies csc->col from the same question."""
        venue = {f"pdc-cdh-col-{D}": ("CD Huachipato vs. CSD Colo-Colo",
                                      _game("pdc", "Primera Chile", "cdh", "col", "CD Huachipato",
                                            "CSD Colo-Colo"))}
        rows = _board(venue)
        _out, tr = _pick(rows, "csc", "Yes")
        assert tr["refusal"] == "yn:no-row"
        h = _resolve(rows, "csc", "Yes")
        assert h["market_slug"] == f"atc-pdc-cdh-col-{D}-col" and h["matched_by"] == "premap_identity"
        assert h["code_translated"] == {"csc": "col"} and "code_pair" not in h

    def test_his_own_identifier_on_the_board_and_refused_is_never_named_around(self, armed):
        own = {f"chi1-cdh-csc-{D}": ("CD Huachipato vs. CSD Colo-Colo",
                                     [_yn(f"atc-chi1-cdh-csc-{D}-csc",
                                          P4.format(a="CSD Colo-Colo", b="CD Huachipato",
                                                    lg="Primera Chile Femenina"))])}
        rows = _board(extra=own)
        assert _resolve(rows, "csc", "Yes") is None
        assert _explain(rows, "csc", "Yes")["split"] == "yn:league-slot"

    def test_the_alias_arm_still_owns_a_shared_suffix(self, armed):
        from tests.test_c2_yesno_alias import EXPECT, FEED as C2_FEED, _board as _c2
        slug, title, ev, oc = C2_FEED[3]
        h = asyncio.run(premap.resolve(_Pool(_c2()), title, ev, oc, slug))
        assert (h["market_slug"], h["intent"], h["matched_by"], h["league_alias"]) == EXPECT[slug]

    def test_never_reused_for_another_date(self, armed):
        later = {"pdc-hua-col-2026-09-13": ("CD Huachipato vs. CSD Colo-Colo", [
            _yn("atc-pdc-hua-col-2026-09-13-col",
                "Will CSD Colo-Colo win against CD Huachipato in the Primera Chile match scheduled "
                "for Sep 13, 2026?")])}
        rows = _board(extra=later)
        h = asyncio.run(premap.resolve(_Pool(rows), "Will CSD Colo-Colo win on 2026-09-13?", HUA_COL,
                                       "Yes", "chi1-cdh-csc-2026-09-13-csc"))
        assert h["market_slug"] == "atc-pdc-hua-col-2026-09-13-col" and h["witness"] == h["market_slug"]
        assert _resolve(rows, "csc", "Yes")["market_slug"] == COL

    def test_the_hit_keeps_source_premap_in_the_mirror(self, armed):
        from tests.test_c1_round2 import _premap_pool
        from tests.test_mirror_maps_the_copy_lane import _yesno_fills
        from tests.test_mirror_shadow import CID
        slug, title, ev = FEED["csc"]
        fills = _yesno_fills(slug, title, ev, slug.rsplit("-", 1)[0])
        m = asyncio.run(ms.map_market(_premap_pool(fills, _board()), fills, None, whale="rn1",
                                      condition_id=CID))
        assert m and m["source"] == "premap" and m["us_slug"] == COL
        ms._map_cache.clear()

    def test_dark_every_row_answers_none(self, dark):
        rows = _board()
        for key in ("csc", "cdh", "hc-draw", "dep", "gdr", "cg-draw"):
            assert _resolve(rows, key, "Yes", titles=HIS_ML) is None, key
        ex = _explain(rows, "csc", "Yes")
        assert ex["split"] == "yn:would-resolve" and ex["yn_c2"]["matched_by"] == "premap_names"


# ------------------------------------------- Liga Portugal: the venue's own date

# the venue's rows for his three por games, verbatim (render-ops
# league-rows-por 14:15Z, scratchpad/hard2/league_rows_por_1415.log:238-246;
# the log cuts the -gil / -acv event title at "Academico de Viseu", the
# draw question carries the full name): under `ligpor`, DATED 2026-09-05
# where his slugs say 2026-09-06, under his codes for gil-acv, one shared
# code for vit-cas (his gui-cas), none for scl-rav (his cds-rio).
D5 = "2026-09-05"
POR_VENUE = {
    f"ligpor-vit-cas-{D5}": ("Vitoria SC Guimaraes vs. Casa Pia Lisbon", [
        _yn(f"atc-ligpor-vit-cas-{D5}-cas",
            "Will Casa Pia Lisbon win against Vitoria SC Guimaraes in the Liga Portugal match "
            "scheduled for Sep 5, 2026?"),
        _yn(f"atc-ligpor-vit-cas-{D5}-vit",
            "Will Vitoria SC Guimaraes win against Casa Pia Lisbon in the Liga Portugal match "
            "scheduled for Sep 5, 2026?"),
        _yn(f"atc-ligpor-vit-cas-{D5}-draw",
            "Will the Liga Portugal match Vitoria SC Guimaraes vs Casa Pia Lisbon scheduled for "
            "Sep 5, 2026 end in a draw?")]),
    f"ligpor-scl-rav-{D5}": ("Santa Clara Azores vs. Rio Ave FC", [
        _yn(f"atc-ligpor-scl-rav-{D5}-rav",
            "Will Rio Ave FC win against Santa Clara Azores in the Liga Portugal match scheduled "
            "for Sep 5, 2026?"),
        _yn(f"atc-ligpor-scl-rav-{D5}-scl",
            "Will Santa Clara Azores win against Rio Ave FC in the Liga Portugal match scheduled "
            "for Sep 5, 2026?"),
        _yn(f"atc-ligpor-scl-rav-{D5}-draw",
            "Will the Liga Portugal match Santa Clara Azores vs Rio Ave FC scheduled for Sep 5, "
            "2026 end in a draw?")]),
    f"ligpor-gil-acv-{D5}": ("Gil Vicente Barcelos vs. Academico de Viseu FC", [
        _yn(f"atc-ligpor-gil-acv-{D5}-acv",
            "Will Academico de Viseu FC win against Gil Vicente Barcelos in the Liga Portugal "
            "match scheduled for Sep 5, 2026?"),
        _yn(f"atc-ligpor-gil-acv-{D5}-gil",
            "Will Gil Vicente Barcelos win against Academico de Viseu FC in the Liga Portugal "
            "match scheduled for Sep 5, 2026?"),
        _yn(f"atc-ligpor-gil-acv-{D5}-draw",
            "Will the Liga Portugal match Gil Vicente Barcelos vs Academico de Viseu FC scheduled "
            "for Sep 5, 2026 end in a draw?")]),
}
# his por markets, verbatim (the same log, statement 1; the draw titles
# are cut there at 44 characters -- the feed's attested wording)
POR_FEED = [
    (f"por-cds-rio-{D}-cds", "Will CD Santa Clara win on 2026-09-06?", "CD Santa Clara vs. Rio Ave FC"),
    (f"por-cds-rio-{D}-rio", "Will Rio Ave FC win on 2026-09-06?", "CD Santa Clara vs. Rio Ave FC"),
    (f"por-cds-rio-{D}-draw", "Will CD Santa Clara vs. Rio Ave FC end in a draw?", "CD Santa Clara vs. Rio Ave FC"),
    (f"por-gil-acv-{D}-acv", "Will Académico de Viseu FC win on 2026-09-06?",
     "Gil Vicente FC vs. Académico de Viseu FC"),
    (f"por-gil-acv-{D}-gil", "Will Gil Vicente FC win on 2026-09-06?", "Gil Vicente FC vs. Académico de Viseu FC"),
    (f"por-gil-acv-{D}-draw", "Will Gil Vicente FC vs. Académico de Viseu FC end in a draw?",
     "Gil Vicente FC vs. Académico de Viseu FC"),
    (f"por-gui-cas-{D}-cas", "Will Casa Pia AC win on 2026-09-06?", "Vitória SC vs. Casa Pia AC"),
    (f"por-gui-cas-{D}-gui", "Will Vitória SC win on 2026-09-06?", "Vitória SC vs. Casa Pia AC"),
    (f"por-gui-cas-{D}-draw", "Will Vitória SC vs. Casa Pia AC end in a draw?", "Vitória SC vs. Casa Pia AC"),
]


def _dated(venue: dict, d: str) -> dict:
    """The same rows with the venue's date FORCED to `d` (identifiers,
    questions, slugs) -- a fixture, never a tolerance."""
    out = {}
    for ev_slug, (title, mkts) in venue.items():
        out[ev_slug.replace(D5, d)] = (title, [
            dict(m, slug=m["slug"].replace(D5, d),
                 question=m["question"].replace("Sep 5, 2026", "Sep 6, 2026"),
                 marketSides=[dict(s, identifier=s["identifier"].replace(D5, d))
                              for s in m["marketSides"]])
            for m in mkts])
    return out


class TestLigaPortugalOnTheVenuesOwnDate:
    def test_the_date_differs_and_no_arm_considers_the_rows(self, armed):
        """His 2026-09-06 against the venue's 2026-09-05: no key meets
        (his name keys and pair keys carry HIS date), and handed the
        rows every arm refuses yn:no-row -- never a +-1 day tolerance."""
        rows = _board(POR_VENUE)
        for slug, title, ev in POR_FEED:
            for oc in ("Yes", "No"):
                assert asyncio.run(premap.resolve(_Pool(rows), title, ev, oc, slug)) is None, slug
            ex = asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, "Yes", slug))
            assert ex["step"] == "no_key_intersection" and ex["rows"] == 0, slug
            # the census: no atc row on HIS date carries either code
            assert ex["split"] == "venue:league-unlisted", slug
            tr: dict = {}
            assert premap._yn_pick(rows, "Yes", title, slug, ev, tr) == []
            assert tr["refusal"] == "yn:no-row", (slug, tr)

    def test_with_the_date_forced_equal_the_names_refuse_by_name(self, armed):
        """'Vitoria SC Guimaraes' is not 'Vitória SC', 'Casa Pia Lisbon'
        not 'Casa Pia AC', 'Santa Clara Azores' not 'CD Santa Clara',
        'Gil Vicente Barcelos' not 'Gil Vicente FC' (token-set equality
        after the fold, no partial match); 'Rio Ave FC' and 'Académico
        de Viseu FC' pass, and a pick needs BOTH sides -- so every one of
        the nine refuses, each under the arm that owns its code shape."""
        rows = _board(_dated(POR_VENUE, D))
        want = {
            # both codes differ (scl-rav vs cds-rio): the names arm
            f"por-cds-rio-{D}-cds": MISMATCH, f"por-cds-rio-{D}-rio": MISMATCH,
            f"por-cds-rio-{D}-draw": MISMATCH,
            # his own codes under another league (gil-acv): the alias arm's
            # names (C2), one arm earlier, the same equality rule
            f"por-gil-acv-{D}-acv": "yn:opp-witness", f"por-gil-acv-{D}-gil": "yn:subj",
            f"por-gil-acv-{D}-draw": "yn:draw-names",
            # one shared code (vit-cas vs gui-cas), BOTH clubs named otherwise:
            # no key of his meets (no name, no pair, no matchup) -- C5's
            # own lookup fact (§11: reachable through the event title
            # alone); the census reads listed, keys unmet
            f"por-gui-cas-{D}-cas": None, f"por-gui-cas-{D}-gui": None, f"por-gui-cas-{D}-draw": None,
        }
        for slug, title, ev in POR_FEED:
            for oc in ("Yes", "No"):
                assert asyncio.run(premap.resolve(_Pool(rows), title, ev, oc, slug)) is None, slug
            ex = asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, "Yes", slug))
            if want[slug] is None:
                assert ex["step"] == "no_key_intersection" and "split" not in ex, slug
                assert ex["league_alias_probe"]["would_have_hit"] is True, slug
            else:
                assert (ex["step"], ex["split"]) == ("no_side_match", want[slug]), (slug, ex.get("yn_c2"))
        # the two names that DO pass, alone, admit nothing
        assert pmus._yn_name_match("rio ave fc", "rio ave fc")
        assert pmus._yn_name_match("academico de viseu fc", "academico de viseu fc")
        for a, b in (("vitoria sc guimaraes", "vitoria sc"), ("casa pia lisbon", "casa pia ac"),
                     ("santa clara azores", "cd santa clara"), ("gil vicente barcelos", "gil vicente fc")):
            assert not pmus._yn_name_match(a, b), (a, b)
