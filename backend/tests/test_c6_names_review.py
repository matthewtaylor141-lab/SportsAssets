"""M3 review pins (2026-09-07): the names-witnessed row (C6-N) and the
pair key (R1) against the mandate's adversarial cases, on the verbatim
rows -- the Liga Portugal rows from league-rows-por 14:15Z
(league_rows_por_1415.log), the Primera Chile rows from esports-chi-rows
13:52Z (test_c6_names.VENUE) -- plus the killers for the four mutants the
builder's suite let live (M05 the candidate date, M11 the four-argument
identity form, M20 a -draw identifier as a per-team candidate, M22 the
stored-title refusal on the census split).

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio

import pytest

from sportsassets.workers import premap
from tests.test_c6_names import (COL, D, FEED, HC_DRAW, HUA, P4, VENUE, _board, _game, _pick, _Pool,
                                 _resolve, _short, _yn)

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
AMBIG, SHEAR, MISMATCH, UNWIT = ("yn:names-ambiguous", "yn:names-shear", "yn:names-mismatch",
                                 "yn:names-unwitnessed")


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _run(rows, title, ev, oc, slug, titles=None):
    h = asyncio.run(premap.resolve(_Pool(rows, titles), title, ev, oc, slug))
    ex = asyncio.run(premap.resolve_explain(_Pool(rows, titles), title, ev, oc, slug))
    return h, ex


# the venue's Liga Portugal listing, verbatim (league_rows_por_1415.log, table 2):
# HIS codes gil-acv under ligpor, dated 2026-09-05 against his 2026-09-06
LIGPOR_05 = {"ligpor-gil-acv-2026-09-05": ("Gil Vicente Barcelos vs. Academico de Viseu FC", [
    _yn("atc-ligpor-gil-acv-2026-09-05-gil",
        "Will Gil Vicente Barcelos win against Academico de Viseu FC in the Liga Portugal match "
        "scheduled for Sep 5, 2026?"),
    _yn("atc-ligpor-gil-acv-2026-09-05-acv",
        "Will Academico de Viseu FC win against Gil Vicente Barcelos in the Liga Portugal match "
        "scheduled for Sep 5, 2026?"),
    _yn("atc-ligpor-gil-acv-2026-09-05-draw",
        "Will the Liga Portugal match Gil Vicente Barcelos vs Academico de Viseu FC scheduled for "
        "Sep 5, 2026 end in a draw?")])}
T_GIL, S_GIL = "Will Gil Vicente FC win on 2026-09-06?", f"por-gil-acv-{D}-gil"
T_ACV, S_ACV = "Will Académico de Viseu FC win on 2026-09-06?", f"por-gil-acv-{D}-acv"
T_GD, S_GD = "Will Gil Vicente FC vs. Académico de Viseu FC end in a draw?", f"por-gil-acv-{D}-draw"
EV_GIL = "Gil Vicente FC vs. Académico de Viseu FC"
EV_GUI = "Vitória SC vs. Casa Pia AC"


class TestTheVerbatimLigaPortugalRows:
    def test_the_venues_09_05_listing_never_meets_his_09_06_slug(self, armed):
        """The 14:15Z read: `atc-ligpor-gil-acv-2026-09-05-*` carries BOTH
        his codes on the day before his -- the pair key and the name
        keys carry his date, so nothing is fetched (+-1 day)."""
        rows = _board(LIGPOR_05)
        for t, s in ((T_GIL, S_GIL), (T_ACV, S_ACV), (T_GD, S_GD)):
            h, ex = _run(rows, t, EV_GIL, "Yes", s)
            assert h is None and (ex["step"], ex["rows"]) == ("no_key_intersection", 0), s

    def test_redated_to_his_day_the_alias_arm_owns_it_and_refuses_on_the_names(self, armed):
        """Both codes shared under another league code is C2's shape:
        the names arm never runs, and the alias witness refuses 'Gil
        Vicente Barcelos' against his 'Gil Vicente FC' by name."""
        venue = {k.replace("09-05", "09-06"): (t, [_yn(m["slug"].replace("09-05", "09-06"),
                                                      m["question"].replace("Sep 5", "Sep 6"))
                                                  for m in ms]) for k, (t, ms) in LIGPOR_05.items()}
        rows = _board(venue)
        for t, s, why in ((T_GIL, S_GIL, "yn:subj"), (T_ACV, S_ACV, "yn:opp-witness"),
                          (T_GD, S_GD, "yn:draw-names")):
            h, ex = _run(rows, t, EV_GIL, "Yes", s)
            assert h is None and (ex["step"], ex["split"]) == ("no_side_match", why), s
            assert "yn_c6" not in ex and ex["yn_c2"].get("matched_by") is None

    def test_vitoria_sc_guimaraes_and_casa_pia_lisbon_are_not_his_clubs(self, armed):
        """'Vitoria SC Guimaraes' vs his 'Vitória SC', 'Casa Pia Lisbon' vs
        his 'Casa Pia AC' (the venue's vit-cas, his gui-cas): token-set
        equality refuses both, whether one code is shared (C5's shape)
        or none (the names arm)."""
        assert not premap.pmus._yn_name_match("vitoria sc guimaraes", "vitoria sc")
        assert not premap.pmus._yn_name_match("casa pia lisbon", "casa pia ac")
        for va, vb in (("vit", "cas"), ("vsg", "cpl")):
            venue = {f"ligpor-{va}-{vb}-{D}": ("Vitoria SC Guimaraes vs. Casa Pia Lisbon",
                                               _game("ligpor", "Liga Portugal", va, vb,
                                                     "Vitoria SC Guimaraes", "Casa Pia Lisbon"))}
            rows = _board(venue)
            for t, s in (("Will Vitória SC win on 2026-09-06?", f"por-gui-cas-{D}-gui"),
                         ("Will Casa Pia AC win on 2026-09-06?", f"por-gui-cas-{D}-cas"),
                         ("Will Vitória SC vs. Casa Pia AC end in a draw?", f"por-gui-cas-{D}-draw")):
                h, ex = _run(rows, t, EV_GUI, "Yes", s)
                assert h is None and ex["step"] == "no_key_intersection", (va, s)
                tr: dict = {}
                assert premap._yn_pick(rows, "Yes", t, s, EV_GUI, tr) == []
                assert tr["refusal"] in ("yn:no-row", "yn:draw-names", MISMATCH), (va, s, tr)


class TestTheSegmentAndTheTwin:
    def test_a_first_half_row_carrying_the_full_game_question_is_never_the_row(self, armed):
        """A -fh- identifier with the P4 full-game wording: not keyed by
        name at the sweep, not a candidate at the read; with the real
        -col row absent the arm reads yn:no-row, with it present the
        real row alone."""
        q = VENUE[f"pdc-hua-col-{D}"][1][0]["question"]
        assert premap.venue_name_keys(q, f"atc-pdc-hua-col-{D}-fh-col") == []
        fh = _yn(f"atc-pdc-hua-col-{D}-fh-col", q)
        venue = dict(VENUE)
        venue[f"pdc-hua-col-{D}"] = (VENUE[f"pdc-hua-col-{D}"][0], [fh] + VENUE[f"pdc-hua-col-{D}"][1][1:])
        rows = _board(venue)
        assert not any(r["identifier"].endswith("-fh-col") for r, _h in premap._yn_names_rows(rows, D, 1))
        h, ex = _run(rows, *FEED["csc"][1:], "Yes", FEED["csc"][0])
        assert h is None and ex["split"] == "yn:no-row"
        venue[f"pdc-hua-col-{D}"] = (VENUE[f"pdc-hua-col-{D}"][0], [fh] + VENUE[f"pdc-hua-col-{D}"][1])
        assert _short(_resolve(_board(venue), "csc", "Yes")) == (COL, "yes", LONG, "premap_names")

    def test_a_second_events_row_at_his_position_naming_his_pair_is_ambiguous(self, armed):
        """The venue's -uco row (another pair's identifier) mis-wording
        his pair, keyed by the sweep on its own subject: two rows at his
        position pass and the arm refuses -- never the first."""
        venue = dict(VENUE)
        venue[f"pdc-pal-uco-{D}"] = ("CD Palestino vs. CD Universidad de Concepcion", [
            _yn(f"atc-pdc-pal-uco-{D}-uco", P4.format(a="CSD Colo-Colo", b="CD Huachipato", lg="Primera Chile")),
            _yn(f"atc-pdc-pal-uco-{D}-pal", P4.format(a="CD Palestino", b="CD Universidad de Concepcion",
                                                      lg="Primera Chile"))])
        rows = _board(venue)
        h, ex = _run(rows, *FEED["csc"][1:], "Yes", FEED["csc"][0])
        assert h is None and ex["split"] == AMBIG
        assert ex["yn_c2"]["candidates"] == [COL, f"atc-pdc-pal-uco-{D}-uco"]

    def test_scope_twins_are_screened_and_the_real_row_maps(self, armed):
        for lg, phrase, a, b in (("pdcp", "Primera Chile Primavera", "CD Huachipato", "CSD Colo-Colo"),
                                 ("pdcu", "Primera Chile U21", "CD Huachipato", "CSD Colo-Colo"),
                                 ("pdc2", "Primera Chile", "CD Huachipato B", "CSD Colo-Colo B")):
            twin = {f"{lg}-hua-col-{D}": (f"{a} vs. {b}", _game(lg, phrase, "hua", "col", a, b))}
            rows = _board(extra=twin)
            assert _short(_resolve(rows, "csc", "Yes")) == (COL, "yes", LONG, "premap_names"), lg
            assert _short(_resolve(rows, "hc-draw", "Yes")) == (HC_DRAW, "yes", LONG, "premap_names"), lg

    @pytest.mark.xfail(strict=True, reason="M3 review F2: the moneyline arm reads the twin's evidence "
                                           "only at his position; the draw arm reads both positions")
    def test_a_twin_witnessed_only_by_the_other_sides_row_is_ambiguous_for_the_moneyline_too(self, armed):
        """A second listing of his pair under `pdcx` whose row at HIS
        position is not in the fetch (its -hua row is): the draw arm
        refuses yn:names-ambiguous from that row; the moneyline arm
        should read the same evidence and refuse the same way."""
        twin = {f"pdcx-hua-col-{D}": ("CD Huachipato vs. CSD Colo-Colo", [
            _yn(f"atc-pdcx-hua-col-{D}-hua", P4.format(a="CD Huachipato", b="CSD Colo-Colo", lg="Primera Chile"))])}
        rows = _board(extra=twin)
        h, ex = _run(rows, *FEED["hc-draw"][1:], "Yes", FEED["hc-draw"][0])
        assert h is None and ex["split"] == AMBIG
        h, ex = _run(rows, *FEED["csc"][1:], "Yes", FEED["csc"][0])
        assert h is None and ex["split"] == AMBIG

    def test_the_venue_listing_the_pair_in_the_other_order_refuses(self, armed):
        venue = {f"pdc-col-hua-{D}": ("CSD Colo-Colo vs. CD Huachipato",
                                      _game("pdc", "Primera Chile", "col", "hua", "CSD Colo-Colo", "CD Huachipato"))}
        rows = _board(venue)
        for key, why in (("csc", "yn:no-row"), ("cdh", "yn:no-row"), ("hc-draw", SHEAR)):
            h, ex = _run(rows, *FEED[key][1:], "Yes", FEED[key][0])
            assert h is None and ex["split"] == why, key


class TestTheMutantsTheSuiteLetLive:
    def test_m05_a_candidates_identifier_date_is_his_date_before_its_question_is_read(self, armed):
        """A venue row whose identifier is dated 2026-09-07 and whose
        question says Sep 6 (the two disagree): the pure arm handed the
        row must not pick it -- the identifier's date screens first."""
        venue = {"pdc-hua-col-2026-09-07": ("CD Huachipato vs. CSD Colo-Colo", [
            _yn("atc-pdc-hua-col-2026-09-07-col", P4.format(a="CSD Colo-Colo", b="CD Huachipato", lg="Primera Chile")),
            _yn("atc-pdc-hua-col-2026-09-07-hua", P4.format(a="CD Huachipato", b="CSD Colo-Colo", lg="Primera Chile"))])}
        rows = _board(venue)
        assert premap._yn_names_rows(rows, D, 1) == []
        out, tr = _pick(rows, "csc", "Yes")
        assert out == [] and tr["refusal"] == "yn:no-row"
        out, tr = _pick(rows, "hc-draw", "Yes")
        assert out == [] and tr["refusal"] == "yn:no-row"

    def test_m11_the_four_argument_identity_form_never_reaches_the_names_draw(self, armed):
        rows = _board()
        assert premap.yn_identity_rows(rows, "Yes", FEED["hc-draw"][1], FEED["hc-draw"][0]) == []
        assert premap.yn_identity_rows(rows, "Yes", FEED["csc"][1], FEED["csc"][0]) == []
        tr: dict = {}
        assert premap._yn_pick(rows, "Yes", FEED["hc-draw"][1], FEED["hc-draw"][0], None, tr,
                               identity_only=True) == []
        assert tr["refusal"] == "yn:no-row"

    def test_m20_a_draw_identifier_wearing_the_per_team_question_is_no_candidate(self, armed):
        venue = dict(VENUE)
        venue[f"pdc-hua-col-{D}"] = ("CD Huachipato vs. CSD Colo-Colo", [
            _yn(f"atc-pdc-hua-col-{D}-draw", P4.format(a="CSD Colo-Colo", b="CD Huachipato", lg="Primera Chile")),
            _yn(f"atc-pdc-hua-col-{D}-hua", P4.format(a="CD Huachipato", b="CSD Colo-Colo", lg="Primera Chile"))])
        rows = _board(venue)
        assert not any(r["identifier"].endswith("-draw") for r, _h in premap._yn_names_rows(rows, D, 1))
        h, ex = _run(rows, *FEED["csc"][1:], "Yes", FEED["csc"][0])
        assert h is None and ex["split"] == "yn:no-row"

    def test_m22_the_stored_title_paths_own_refusal_rides_the_census_split(self, armed):
        """His -dep title stored as 'Deportivo Cali': the pure arm says
        unwitnessed, the stored-title arm names the mismatch -- the
        census must read the latter."""
        rows = _board()
        bad = {f"col1-dep-mif-{D}-dep": ["Will Deportivo Cali win on 2026-09-06?"]}
        h, ex = _run(rows, FEED["mif"][1], None, "Yes", FEED["mif"][0], titles=bad)
        assert h is None
        assert (ex["step"], ex["split"]) == ("no_side_match", MISMATCH)
        assert ex["yn_c6"]["refusal"] == MISMATCH and ex["yn_c2"]["refusal"] == UNWIT


class TestTheKeyAndTheSwitch:
    def test_the_pair_key_is_never_a_bare_date_nor_dateless(self):
        for k in ("2026-09-06", "x-2026-09-06", "atc-2026-09-06", "a-b-c-d-2026-09-06", "kbk-tro"):
            assert premap._pair_key(k, D) is None, k
        assert premap._pair_key("nor-kbk-tro-2026-09-06", D) == "kbk-tro-2026-09-06"
        assert premap._pair_key("nor-kbk-tro-2026-09-06", "") is None
        # the whole set a moneyline emits, dated and never shorter than two team tokens + date
        for k in premap.event_keys_for(None, "nor-kbk-tro-2026-09-06-kbk"):
            assert k.endswith(D) and len(k[:-len(D)].strip("-").split("-")) >= 2, k

    def test_dark_the_stored_title_is_never_read_and_nothing_maps(self, dark):
        rows = _board()
        pool = _Pool(rows, {f"col1-dep-mif-{D}-dep": [FEED["dep"][1]]})
        assert asyncio.run(premap.resolve(pool, FEED["mif"][1], None, "Yes", FEED["mif"][0])) is None
        assert pool.reads == ["us_premap"]
        for key in ("csc", "cdh", "hc-draw", "gdr", "cg-draw"):
            assert _resolve(rows, key, "No") is None, key

    def test_the_hit_carries_no_table_and_a_second_date_certifies_from_its_own_rows(self, armed):
        rows = _board()
        h = _resolve(rows, "csc", "Yes")
        assert h["code_pair"] == {"cdh": "hua", "csc": "col"} and h["witness"] == COL
        # the same pair on another date with the venue's row absent: nothing
        h13 = asyncio.run(premap.resolve(_Pool(rows), "Will CSD Colo-Colo win on 2026-09-13?",
                                         "CD Huachipato vs. CSD Colo-Colo", "Yes", "chi1-cdh-csc-2026-09-13-csc"))
        assert h13 is None
        # and the mirror of the row: his No on Huachipato is the -hua row's no, never -col
        assert _short(_resolve(rows, "cdh", "No")) == (HUA, "no", SHORT, "premap_names")
