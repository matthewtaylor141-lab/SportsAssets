"""C6 (2026-09-07): the exact score (C6-ES) and the halftime result
(C6-HT) by identity -- the two families C3_ABSENT refused by name because
c3_rows_2041.log listed none (that preset filtered 'atc-%' out). The
venue lists both under the moneyline prefix.

The venue's rows are reproduced VERBATIM: the two Premier League games
of 2026-09-06 from epl_rows_1338.log:241-316 (the P4 rows, the draws,
every exact-score identifier with its question, side, intent and the
PHANTOM line the sweep stamped), the -fh- rows of the same games from
nf_venue_1350.log:273-312 (the halftime per-team rows, the tied row,
the -fh-exact-score- twins) and its suffix census rows 245-247 (the
second-half rows), La Liga's esp-sev rows from premap_rows_1909.log:62-
112. His rows are nf_his_1350.log, verbatim. The Ligue 1 event is built
on the esp-sev shape with the C2 dump's P4 wording (test_c2_yesno_alias
VENUE_EXTRA) -- an expectation about the alias event's wording, as the
C2/C3 fixtures made it; every decision below reads the question.

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets.copy_sports import family_of
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"

EVE_MNU = "Everton FC vs. Manchester United FC"
ARS_CFC = "Arsenal FC vs. Chelsea FC"
ESP_SEV = "RCD Espanyol de Barcelona vs. Sevilla FC"
OLM_PFC = "Olympique de Marseille vs. Paris FC"
JUV_MIL = "Juventus FC vs. AC Milan"

P4 = "Will {a} win against {b} in the {lg} match scheduled for Sep 6, 2026?"
DRAWQ = "Will the {lg} match {a} vs {b} scheduled for Sep 6, 2026 end in a draw?"


def _yn(ident: str, q: str) -> dict:
    """The venue's own side expansion: both sides share the identifier;
    `long` names the intent (the dumps' intents)."""
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _tsc(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Over", "long": True},
        {"identifier": ident, "description": "Under", "long": False}]}


# the 17 exact-score identifiers of every listed game and the venue's
# question for each (epl_rows_1338.log:247-278, :283-314; premap_rows_1909
# .log:66-97 -- the same grammar on lal-esp-sev)
SCORES = ["0-0", "0-1", "0-2", "0-3", "1-0", "1-1", "1-2", "1-3", "2-0", "2-1", "2-2",
          "2-3", "3-0", "3-1", "3-2"]


def _exact_q(va: str, vb: str, score: str) -> str:
    x, y = (int(t) for t in score.split("-"))
    if x > y:
        return f"Will {va} vs {vb} finish {va} wins {x}-{y}?"
    if x < y:
        return f"Will {va} vs {vb} finish {vb} wins {y}-{x}?"
    return f"Will {va} vs {vb} finish Draw {x}-{x}?"


def _game(lg: str, va: str, vb: str, A: str, B: str, lgname: str, *, exact=True,
          fh=(), fh_exact=(), sh=False) -> list[dict]:
    e = f"{lg}-{va}-{vb}-{D}"
    VA, VB = va.upper(), vb.upper()
    out = [
        _yn(f"atc-{e}-{va}", P4.format(a=A, b=B, lg=lgname)),
        _yn(f"atc-{e}-{vb}", P4.format(a=B, b=A, lg=lgname)),
        _yn(f"atc-{e}-draw", DRAWQ.format(a=A, b=B, lg=lgname)),
    ]
    if exact:
        out += [_yn(f"atc-{e}-exact-score-{s}", _exact_q(VA, VB, s)) for s in SCORES]
        out.append(_yn(f"atc-{e}-exact-score-other", f"Will {VA} vs {VB} finish Other?"))
    for t in fh:
        # nf_venue_1350.log:273-276, 299-300; premap_rows_1909.log:100-101
        S, O = (A, B) if t == va else (B, A)
        out.append(_yn(f"atc-{e}-fh-{t}", f"Will {S} lead {O} at halftime?"))
    if fh:
        # :277-278, :297-298; premap_rows_1909.log:98-99
        out.append(_yn(f"atc-{e}-fh-draw", f"Will {VA} vs {VB} be tied at halftime?"))
    for s in fh_exact:
        # :279-296, :301-312 -- the halftime twin of the exact score
        q = _exact_q(VA, VB, s).replace("finish ", "be ").replace("?", " at halftime?")
        out.append(_yn(f"atc-{e}-fh-exact-score-{s}", q))
    if sh:
        # nf_venue_1350.log:245-247 (the suffix census, with the question)
        out += [_yn(f"atc-{e}-sh-draw", f"Will the second half of {VA} vs {VB} be a draw?"),
                _yn(f"atc-{e}-sh-{va}", f"Will {A} win the second half against {B}?"),
                _yn(f"atc-{e}-sh-{vb}", f"Will {B} win the second half against {A}?")]
    out.append(_tsc(f"tsc-{e}-2pt5", f"Will the total in {VA} vs {VB} be more than 2.5?"))
    return out


VENUE = {
    f"epl-eve-mnu-{D}": (EVE_MNU, _game(
        "epl", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League",
        fh=("eve",), fh_exact=("0-0", "0-1", "0-2", "1-0", "1-1", "1-2"), sh=True)),
    f"epl-ars-cfc-{D}": (ARS_CFC, _game(
        "epl", "ars", "cfc", "Arsenal FC", "Chelsea FC", "Premier League",
        fh=("ars", "cfc"), fh_exact=("0-0", "0-1", "0-2", "1-0", "1-1", "1-2", "2-0", "2-1"))),
    f"lal-esp-sev-{D}": (ESP_SEV, _game(
        "lal", "esp", "sev", "RCD Espanyol de Barcelona", "Sevilla FC", "La Liga",
        fh=("esp",), fh_exact=("0-0", "0-1", "0-2", "1-0", "1-1", "1-2"))),
    # the alias event: his fl1 is the venue's lg1 (C2); rows on the
    # esp-sev shape, P4 wording from the C2 dump
    f"lg1-olm-pfc-{D}": (OLM_PFC, _game(
        "lg1", "olm", "pfc", "Olympique de Marseille", "Paris FC", "Ligue 1",
        fh=("olm", "pfc"))),
    # an event that lists neither family (the bra / ecu1 shape: 6 atc rows)
    f"sea-juv-mil-{D}": (JUV_MIL, _game(
        "sea", "juv", "mil", "Juventus FC", "AC Milan", "Serie A", exact=False)),
}

# his feed (nf_his_1350.log, verbatim): (market_slug, market_title,
# markets.event_title, outcome). The event title is given as the matchup
# alone: its stored ' - Exact Score' / ' - Halftime Result' suffix is the
# key grammar M1 builds (event_keys_for, _yn_event_sides), not this lane.
FEED = {
    "es-2-1": (f"epl-eve-mun-{D}-exact-score-2-1",
               "Exact Score: Everton FC 2 - 1 Manchester United FC?", EVE_MNU, "Yes"),
    "es-1-2": (f"epl-eve-mun-{D}-exact-score-1-2",
               "Exact Score: Everton FC 1 - 2 Manchester United FC?", EVE_MNU, "No"),
    "es-1-1": (f"epl-eve-mun-{D}-exact-score-1-1",
               "Exact Score: Everton FC 1 - 1 Manchester United FC?", EVE_MNU, "Yes"),
    "es-0-0": (f"epl-eve-mun-{D}-exact-score-0-0",
               "Exact Score: Everton FC 0 - 0 Manchester United FC?", EVE_MNU, "No"),
    "es-3-0": (f"epl-eve-mun-{D}-exact-score-3-0",
               "Exact Score: Everton FC 3 - 0 Manchester United FC?", EVE_MNU, "No"),
    "ars-es-2-1": (f"epl-ars-che-{D}-exact-score-2-1",
                   "Exact Score: Arsenal FC 2 - 1 Chelsea FC?", ARS_CFC, "Yes"),
    "sea-es-2-0": (f"sea-juv-mil-{D}-exact-score-2-0",
                   "Exact Score: Juventus FC 2 - 0 AC Milan?", JUV_MIL, "No"),
    "ht-away": (f"epl-ars-che-{D}-halftime-result-away", "Chelsea FC leading at halftime?",
                ARS_CFC, "Yes"),
    "ht-away-no": (f"epl-ars-che-{D}-halftime-result-away", "Chelsea FC leading at halftime?",
                   ARS_CFC, "No"),
    "ht-home": (f"epl-ars-che-{D}-halftime-result-home", "Arsenal FC leading at halftime?",
                ARS_CFC, "Yes"),
    "ht-mun": (f"epl-eve-mun-{D}-halftime-result-away",
               "Manchester United FC leading at halftime?", EVE_MNU, "Yes"),
    "ht-draw": (f"fl1-olm-pfc-{D}-halftime-result-draw",
                "Olympique de Marseille vs. Paris FC: Draw at halftime?", OLM_PFC, "Yes"),
    "ht-sea": (f"sea-juv-mil-{D}-halftime-result-draw",
               "Juventus FC vs. AC Milan: Draw at halftime?", JUV_MIL, "Yes"),
}


def _board(venue: dict | None = None, extra: dict | None = None, drop=()) -> list[dict]:
    """Rows built by the sweep's own row builder and keyed the way the
    sweep keys them (event_keys_for on the venue's event title + slug)."""
    rows: list[dict] = []
    for src in (venue if venue is not None else VENUE, extra or {}):
        for ev_slug, (ev_title, mkts) in src.items():
            keys = premap.event_keys_for(ev_title, ev_slug)
            for m in mkts:
                if m["slug"] in drop:
                    continue
                for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m):
                    r["event_keys"] = keys
                    rows.append(r)
    return rows


def _stored(rows: list[dict]) -> list[dict]:
    """The rows AS us_premap HOLDS THEM TONIGHT: the sweep before this
    build stamped the phantom line ('1' out of 'wins 2-1', '0' out of
    '3-0') on every exact-score row (epl_rows_1338.log:247-314)."""
    out = []
    for r in rows:
        r = dict(r)
        if "-exact-score-" in r["identifier"] and not r["identifier"].endswith("other"):
            r["line"] = premap._question_line(r["question"])
            assert r["line"] in ("0", "1", "2", "3"), r
        out.append(r)
    return out


class _Pool:
    """us_premap by key intersection; the C5 title read finds nothing."""

    def __init__(self, rows):
        self.rows = rows
        self.reads: list[str] = []

    async def fetch(self, sql, *a):
        if "us_premap" in sql:
            self.reads.append("us_premap")
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        self.reads.append("titles")
        return []


def _resolve(rows, key, *, title=None, ev="keep", oc=None):
    slug, t, evt, o = FEED[key]
    return asyncio.run(premap.resolve(_Pool(rows), title or t, evt if ev == "keep" else ev,
                                      oc or o, slug))


def _explain(rows, key, *, title=None, ev="keep", oc=None):
    slug, t, evt, o = FEED[key]
    return asyncio.run(premap.resolve_explain(_Pool(rows), title or t,
                                              evt if ev == "keep" else ev, oc or o, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


def _reword(rows, ident, q):
    return [dict(r, question=q) if r["identifier"] == ident else dict(r) for r in rows]


def _pick(rows, key, *, title=None, ev="keep", slug=None):
    s, t, evt, o = FEED[key]
    tr: dict = {}
    kept = [r for r in rows if r["identifier"].startswith("atc-")]
    hit = premap.c6_pick(kept, o, title or t, slug or s, evt if ev == "keep" else ev, tr)
    return hit, tr


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


E = f"atc-epl-eve-mnu-{D}"
AC = f"atc-epl-ars-cfc-{D}"
MNU, EVE = f"{E}-mnu", f"{E}-eve"


# ------------------------------------------------- (a) (b) (c): the exact score

class TestAtoCTheExactScoreLandsOnItsOwnIdentifier:
    def test_a_his_2_1_is_the_venues_2_1_row_with_mun_translated(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "es-2-1")
        assert _short(h) == (f"{E}-exact-score-2-1", "yes", LONG, "premap_exact_score")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        assert h["title"] == "Will EVE vs MNU finish EVE wins 2-1?"
        assert "league_alias" not in h
        ex = _explain(rows, "es-2-1")
        assert ex["step"] == "resolves" and ex["detail"] == f"{E}-exact-score-2-1"
        assert ex["matched_by"] == "premap_exact_score"
        assert ex["code_translated"] == {"mun": "mnu"} and ex["witness"] == MNU
        c5 = ex["yn_c5"]
        assert c5["label"] == "yn:code-translated" and c5["witness_src"] == "event"
        assert c5["slug"] == f"epl-eve-mnu-{D}-exact-score-2-1"
        assert c5["lane"]["matched_by"] == "premap_exact_score"
        assert c5["lane"]["admitted"] == f"{E}-exact-score-2-1"
        # the event's own rows refused the untranslated slug by name first
        assert ex["c6"]["refusal"] == "exact:family-absent"

    def test_a_the_same_codes_need_no_translation(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "ars-es-2-1")
        assert _short(h) == (f"{AC}-exact-score-2-1", "yes", LONG, "premap_exact_score")
        assert h["code_translated"] == {"che": "cfc"}
        # a board where his codes ARE the venue's: identity, no C5 trace
        venue = {f"epl-eve-mun-{D}": (EVE_MNU, _game(
            "epl", "eve", "mun", "Everton FC", "Manchester United FC", "Premier League"))}
        rows = _stored(_board(venue))
        h = _resolve(rows, "es-2-1")
        assert _short(h) == (f"atc-epl-eve-mun-{D}-exact-score-2-1", "yes", LONG,
                             "premap_exact_score")
        assert "code_translated" not in h
        ex = _explain(rows, "es-2-1")
        assert ex["step"] == "resolves" and "yn_c5" not in ex
        assert ex["c6"]["matched_by"] == "premap_exact_score" and ex["c6"]["side"] == "yes"

    def test_b_his_no_on_1_2_is_the_short_side_of_mnu_wins_2_1(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "es-1-2")
        assert _short(h) == (f"{E}-exact-score-1-2", "no", SHORT, "premap_exact_score")
        assert h["title"] == "Will EVE vs MNU finish MNU wins 2-1?"
        h = _resolve(rows, "es-3-0")
        assert _short(h) == (f"{E}-exact-score-3-0", "no", SHORT, "premap_exact_score")
        assert h["title"] == "Will EVE vs MNU finish EVE wins 3-0?"

    def test_c_the_draw_scores(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "es-1-1")
        assert _short(h) == (f"{E}-exact-score-1-1", "yes", LONG, "premap_exact_score")
        assert h["title"] == "Will EVE vs MNU finish Draw 1-1?"
        h = _resolve(rows, "es-0-0")
        assert _short(h) == (f"{E}-exact-score-0-0", "no", SHORT, "premap_exact_score")
        assert h["title"] == "Will EVE vs MNU finish Draw 0-0?"

    def test_every_listed_score_of_both_games_maps_and_only_to_itself(self, armed):
        rows = _stored(_board())
        for va, vb, A, B, key in (("eve", "mnu", "Everton FC", "Manchester United FC", "es-2-1"),
                                  ("ars", "cfc", "Arsenal FC", "Chelsea FC", "ars-es-2-1")):
            slug0 = FEED[key][0]
            for s in SCORES:
                x, y = s.split("-")
                slug = slug0.rsplit("-", 2)[0] + f"-{x}-{y}"
                title = f"Exact Score: {A} {x} - {y} {B}?"
                for oc, side, intent in (("Yes", "yes", LONG), ("No", "no", SHORT)):
                    h = asyncio.run(premap.resolve(_Pool(rows), title, FEED[key][2], oc, slug))
                    assert _short(h) == (f"atc-epl-{va}-{vb}-{D}-exact-score-{s}", side, intent,
                                         "premap_exact_score"), (slug, oc)


# ------------------------------------------------- (d): the order, certified

class TestDTheDigitOrderIsCertifiedByTheVenuesWords:
    def test_d_the_1_2_row_reworded_eve_wins_2_1_refuses(self, armed):
        rows = _reword(_stored(_board()), f"{E}-exact-score-1-2",
                       "Will EVE vs MNU finish EVE wins 2-1?")
        assert _resolve(rows, "es-1-2") is None
        ex = _explain(rows, "es-1-2")
        assert ex["step"] == "no_side_match" and ex["split"] == "exact:order-uncertified"
        assert ex["yn_c5"]["lane"]["venue_question"] == "Will EVE vs MNU finish EVE wins 2-1?"

    @pytest.mark.parametrize("q", [
        "Will EVE vs MNU finish MNU wins 2-1?",      # the other winner
        "Will EVE vs MNU finish EVE wins 1-2?",      # min-max, not the venue's grammar
        "Will EVE vs MNU finish EVE wins 3-1?",      # another score
        "Will EVE vs MNU finish Draw 2-1?",          # a draw that is not one
        "Will EVE vs MNU finish Draw 2-2?",
    ])
    def test_the_2_1_row_under_any_other_wording_refuses(self, armed, q):
        rows = _reword(_stored(_board()), f"{E}-exact-score-2-1", q)
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["split"] == "exact:order-uncertified"

    def test_a_draw_identifier_worded_as_a_win_refuses(self, armed):
        rows = _reword(_stored(_board()), f"{E}-exact-score-1-1",
                       "Will EVE vs MNU finish EVE wins 1-1?")
        assert _resolve(rows, "es-1-1") is None
        assert _explain(rows, "es-1-1")["split"] == "exact:order-uncertified"

    def test_the_codes_must_be_the_identifiers_in_its_order(self, armed):
        rows = _reword(_stored(_board()), f"{E}-exact-score-2-1",
                       "Will MNU vs EVE finish EVE wins 2-1?")
        assert _resolve(rows, "es-2-1") is None
        ex = _explain(rows, "es-2-1")
        assert ex["split"] == "exact:codes" and ex["yn_c5"]["lane"]["venue_codes"] == ["mnu", "eve"]

    def test_another_grammar_refuses_by_shape(self, armed):
        rows = _reword(_stored(_board()), f"{E}-exact-score-2-1",
                       "Will the score of EVE vs MNU be 2-1?")
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["split"] == "exact:question-shape"


# ------------------------------------------------- (e): the club witness

class TestETheFirstClubIsVaOnlyThroughTheEventsOwnP4Rows:
    def test_e_his_title_with_the_clubs_swapped_refuses(self, armed):
        rows = _stored(_board())
        t = "Exact Score: Manchester United FC 2 - 1 Everton FC?"
        assert _resolve(rows, "es-2-1", title=t) is None
        ex = _explain(rows, "es-2-1", title=t)
        assert ex["step"] == "no_side_match" and ex["split"] == "exact:club-unwitnessed"
        assert ex["yn_c5"]["lane"]["why"] == "subject:eve"

    def test_a_missing_or_unshaped_p4_row_leaves_the_club_unwitnessed(self, armed):
        venue = {f"epl-eve-mun-{D}": (EVE_MNU, _game(
            "epl", "eve", "mun", "Everton FC", "Manchester United FC", "Premier League"))}
        rows = _stored(_board(venue, drop={f"atc-epl-eve-mun-{D}-mun"}))
        assert _resolve(rows, "es-2-1") is None
        ex = _explain(rows, "es-2-1")
        assert ex["split"] == "exact:club-unwitnessed" and ex["c6"]["why"] == "p4-row-absent:mun"
        rows = _reword(_stored(_board(venue)), f"atc-epl-eve-mun-{D}-eve",
                       "Will Everton FC win the first half against Manchester United FC?")
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["c6"]["why"] == "yn:shape:eve"

    def test_a_title_naming_another_club_or_another_score(self, armed):
        rows = _stored(_board())
        t = "Exact Score: Everton FC 2 - 1 Manchester City FC?"
        assert _resolve(rows, "es-2-1", title=t) is None
        assert _explain(rows, "es-2-1", title=t)["split"] == "exact:club-unwitnessed"
        t = "Exact Score: Everton FC 2 - 0 Manchester United FC?"
        assert _resolve(rows, "es-2-1", title=t) is None
        assert _explain(rows, "es-2-1", title=t)["split"] == "exact:title-score"
        for t in ("Everton FC vs. Manchester United FC: Exact Score 2-1",
                  "Exact Score: Everton FC 2 - 1?", "Will Everton FC win 2-1 on 2026-09-06?"):
            assert _resolve(rows, "es-2-1", title=t) is None
            assert _explain(rows, "es-2-1", title=t)["split"] == "exact:title-shape"


# ------------------------------------------------- (f) (g): absent, other

class TestFGTheFamilyAbsentAndTheOther:
    def test_f_an_event_without_exact_score_rows_is_the_family_gap_it_always_was(self, armed):
        rows = _stored(_board())
        assert _resolve(rows, "sea-es-2-0") is None
        ex = _explain(rows, "sea-es-2-0")
        assert ex["step"] == "unknown_market_type" and ex["split"] == "family_not_listed"
        assert ex["refusal"] == "exact:family-absent" and ex["family"] == "exact_score"
        assert ex["c6"]["refusal"] == "exact:family-absent" and "yn_c5" not in ex
        ctx = {"title": FEED["sea-es-2-0"][1], "event_title": JUV_MIL, "outcome": "No",
               "his_slug": FEED["sea-es-2-0"][0]}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == \
            "unknown_market_type:family_not_listed"
        # the halftime twin alike
        assert _resolve(rows, "ht-sea") is None
        ex = _explain(rows, "ht-sea")
        assert (ex["step"], ex["split"], ex["refusal"]) == \
            ("unknown_market_type", "family_not_listed", "halftime:family-absent")

    def test_a_listed_family_missing_his_score_is_no_row(self, armed):
        rows = _stored(_board(drop={f"{E}-exact-score-1-2"}))
        assert _resolve(rows, "es-1-2") is None
        ex = _explain(rows, "es-1-2")
        assert ex["step"] == "no_side_match" and ex["split"] == "exact:no-row"
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), {
            "title": FEED["es-1-2"][1], "event_title": EVE_MNU, "outcome": "No",
            "his_slug": FEED["es-1-2"][0]})) == "no_side_match:exact:no-row"

    def test_g_other_is_never_a_proposition_his_title_states(self, armed):
        rows = _stored(_board())
        slug = f"epl-eve-mun-{D}-exact-score-other"
        for t in ("Exact Score: Other?", "Exact Score: Everton FC vs. Manchester United FC Other?"):
            assert asyncio.run(premap.resolve(_Pool(rows), t, EVE_MNU, "Yes", slug)) is None
            ex = asyncio.run(premap.resolve_explain(_Pool(rows), t, EVE_MNU, "Yes", slug))
            assert ex["step"] == "no_side_match" and ex["split"] == "exact:other"
        # and the venue's -other row is never any score's row
        rows = _reword(rows, f"{E}-exact-score-2-1", "Will EVE vs MNU finish Other?")
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["split"] == "exact:other"

    def test_the_older_es_form_and_a_segmented_score_refuse_by_shape(self, armed):
        rows = _stored(_board())
        for slug in (f"epl-eve-mun-{D}-es-2-1", f"epl-eve-mun-{D}-first-half-exact-score-2-1",
                     f"epl-eve-mun-{D}-exact-score-2-x"):
            assert family_of(slug) == "exact_score"
            assert asyncio.run(premap.resolve(_Pool(rows), FEED["es-2-1"][1], EVE_MNU, "Yes",
                                              slug)) is None
            ex = asyncio.run(premap.resolve_explain(_Pool(rows), FEED["es-2-1"][1], EVE_MNU,
                                                    "Yes", slug))
            assert ex["step"] == "no_side_match" and ex["split"] == "exact:slug-shape"


# ------------------------------------------------- (h): the phantom line

class TestHThePhantomLineNeverVetoes:
    def test_h_the_rows_as_stored_tonight_carry_the_phantom_and_still_map(self, armed):
        rows = _stored(_board())
        r21 = [r for r in rows if r["identifier"] == f"{E}-exact-score-2-1"]
        assert {r["line"] for r in r21} == {"1"}
        r30 = [r for r in rows if r["identifier"] == f"{E}-exact-score-3-0"]
        assert {r["line"] for r in r30} == {"0"}
        assert _short(_resolve(rows, "es-2-1"))[0] == f"{E}-exact-score-2-1"
        assert _short(_resolve(rows, "es-3-0"))[0] == f"{E}-exact-score-3-0"
        # the lane never runs the line guard
        src = inspect.getsource(premap._c6_pick_exact) + inspect.getsource(premap._c6_side_row)
        assert "_yn_line_ok" not in src and "line_ok" not in src and '"line"' not in src

    def test_the_sweep_stamps_no_line_on_an_exact_score_row_from_now_on(self):
        rows = _board()
        for r in rows:
            if "-exact-score-" in r["identifier"]:
                assert r["line"] == "", r
            elif r["identifier"].startswith("tsc-"):
                assert r["line"] == "2.5", r
        m = _yn(f"{E}-exact-score-3-0", "Will EVE vs MNU finish EVE wins 3-0?")
        built = premap._market_rows({"slug": f"epl-eve-mnu-{D}", "title": EVE_MNU}, m)
        assert [(r["side_norm"], r["intent"], r["line"]) for r in built] == \
            [("yes", LONG, ""), ("no", SHORT, "")]

    def test_a_lined_row_is_not_read_as_a_line_by_any_other_arm_either(self, armed):
        """His total on the same event: the 2-1 row's phantom line '1'
        never made it a candidate for a total, before or after."""
        rows = _stored(_board())
        h = asyncio.run(premap.resolve(_Pool(rows), f"{EVE_MNU}: O/U 2.5", EVE_MNU, "Under",
                                       f"epl-eve-mun-{D}-total-2pt5"))
        assert h["market_slug"] == f"tsc-epl-eve-mnu-{D}-2pt5" and h["intent"] == SHORT


# ------------------------------------------------- (i): the switch

class TestITheSwitchOffIsByteIdentical:
    @pytest.mark.parametrize("key,fam,name", [
        ("es-2-1", "exact_score", "exact:family-absent"),
        ("ht-away", "halftime_result", "halftime:family-absent"),
        ("ht-draw", "halftime_result", "halftime:family-absent"),
    ])
    def test_i_dark_the_family_is_not_listed_as_before(self, dark, key, fam, name):
        rows = _stored(_board())
        assert premap._want_prefixes(FEED[key][0]) is None
        assert _resolve(rows, key) is None
        ex = _explain(rows, key)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "family_not_listed"
        assert ex["family"] == fam and ex["refusal"] == name and ex["c3_on"] is False
        assert "c6" not in ex and "yn_c5" not in ex

    def test_armed_the_prefix_is_the_moneylines(self, armed):
        assert premap._want_prefixes(FEED["es-2-1"][0]) == {"atc"}
        assert premap._want_prefixes(FEED["ht-away"][0]) == {"atc"}
        assert premap._want_prefixes(f"epl-eve-mun-{D}-mun") == {"aec", "atc"}
        assert premap._want_prefixes(f"epl-eve-mun-{D}-total-2pt5") == {"tsc"}


# ------------------------------------------------- (j): the halftime result

class TestJTheHalftimeResult:
    def test_j_chelsea_leading_is_the_venues_fh_cfc_row(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "ht-away")
        assert _short(h) == (f"{AC}-fh-cfc", "yes", LONG, "premap_halftime")
        assert h["code_translated"] == {"che": "cfc"} and h["witness"] == f"{AC}-cfc"
        assert h["title"] == "Will Chelsea FC lead Arsenal FC at halftime?"
        h = _resolve(rows, "ht-away-no")
        assert _short(h) == (f"{AC}-fh-cfc", "no", SHORT, "premap_halftime")
        ex = _explain(rows, "ht-away")
        assert ex["step"] == "resolves" and ex["matched_by"] == "premap_halftime"
        assert ex["yn_c5"]["lane"]["fh_code"] == "cfc"
        assert ex["yn_c5"]["lane"]["admitted"] == f"{AC}-fh-cfc"

    def test_j_arsenal_leading_is_fh_ars(self, armed):
        rows = _stored(_board())
        assert _short(_resolve(rows, "ht-home")) == (f"{AC}-fh-ars", "yes", LONG, "premap_halftime")

    def test_home_away_is_never_read_as_a_position(self, armed):
        """His slug's token is the feed's; the club his TITLE names is the
        row (the brief: the code is read from the identifier, never from
        'home' / 'away')."""
        rows = _stored(_board())
        h = _resolve(rows, "ht-home", title="Chelsea FC leading at halftime?")
        assert _short(h) == (f"{AC}-fh-cfc", "yes", LONG, "premap_halftime")
        src = inspect.getsource(premap._c6_pick_halftime)
        assert '"home"' not in src and '"away"' not in src

    def test_j_the_draw_through_the_league_alias(self, armed):
        rows = _stored(_board())
        h = _resolve(rows, "ht-draw")
        assert _short(h) == (f"atc-lg1-olm-pfc-{D}-fh-draw", "yes", LONG, "premap_halftime")
        assert h["league_alias"] == "fl1->lg1" and "code_translated" not in h
        assert h["title"] == "Will OLM vs PFC be tied at halftime?"
        ex = _explain(rows, "ht-draw")
        assert ex["step"] == "resolves" and ex["league_alias"] == "fl1->lg1"
        assert ex["c6"]["league_alias"] == "fl1->lg1" and "yn_c5" not in ex
        # and a home/away pick under the same alias
        h = asyncio.run(premap.resolve(_Pool(rows), "Paris FC leading at halftime?", OLM_PFC,
                                       "No", f"fl1-olm-pfc-{D}-halftime-result-away"))
        assert _short(h) == (f"atc-lg1-olm-pfc-{D}-fh-pfc", "no", SHORT, "premap_halftime")
        assert h["league_alias"] == "fl1->lg1"

    def test_the_draws_codes_must_be_the_identifiers(self, armed):
        rows = _reword(_stored(_board()), f"atc-lg1-olm-pfc-{D}-fh-draw",
                       "Will ESP vs SEV be tied at halftime?")
        assert _resolve(rows, "ht-draw") is None
        ex = _explain(rows, "ht-draw")
        assert ex["split"] == "halftime:codes" and ex["c6"]["venue_codes"] == ["esp", "sev"]
        rows = _reword(_stored(_board()), f"atc-lg1-olm-pfc-{D}-fh-draw",
                       "Will the first half of OLM vs PFC end level?")
        assert _explain(rows, "ht-draw")["split"] == "halftime:question-shape"

    def test_the_draws_clubs_are_bound_by_the_p4_rows(self, armed):
        rows = _stored(_board(drop={f"atc-lg1-olm-pfc-{D}-pfc"}))
        assert _resolve(rows, "ht-draw") is None
        ex = _explain(rows, "ht-draw")
        assert ex["split"] == "halftime:subject-unwitnessed" and ex["c6"]["why"] == "p4-row-absent:pfc"
        t = "Olympique de Marseille vs. Paris FC: Draw at halftime?"
        rows = _stored(_board())
        assert _resolve(rows, "ht-draw", title="Olympique Lyonnais vs. Paris FC: Draw at halftime?") is None
        ex = _explain(rows, "ht-draw", title="Olympique Lyonnais vs. Paris FC: Draw at halftime?")
        assert ex["split"] == "halftime:subject-unwitnessed" and ex["c6"]["why"] == "subject:olm"
        for bad in (f"{t[:-1]} (Aggregate)?", "Draw at halftime?",
                    "Olympique de Marseille vs. Paris FC: Draw?"):
            assert _resolve(rows, "ht-draw", title=bad) is None
            assert _explain(rows, "ht-draw", title=bad)["split"] == "halftime:title-shape"

    def test_j_a_question_naming_the_other_club_as_subject_is_unwitnessed(self, armed):
        rows = _reword(_stored(_board()), f"{AC}-fh-cfc", "Will Arsenal FC lead Chelsea FC at halftime?")
        assert _resolve(rows, "ht-away") is None
        ex = _explain(rows, "ht-away")
        assert ex["step"] == "no_side_match" and ex["split"] == "halftime:subject-unwitnessed"
        assert ex["yn_c5"]["lane"]["venue_subjects"] == ["arsenal fc", "arsenal fc"]
        # two rows both naming his club: ambiguity refuses
        rows = _reword(_stored(_board()), f"{AC}-fh-ars", "Will Chelsea FC lead Arsenal FC at halftime?")
        assert _resolve(rows, "ht-away") is None
        assert _explain(rows, "ht-away")["split"] == "halftime:ambiguous"
        # the opponent must be his other club
        rows = _reword(_stored(_board()), f"{AC}-fh-cfc", "Will Chelsea FC lead Everton FC at halftime?")
        assert _resolve(rows, "ht-away") is None
        assert _explain(rows, "ht-away")["split"] == "halftime:subject-unwitnessed"

    def test_the_wrong_market_trap_a_second_half_row_is_never_a_candidate(self, armed):
        """eve-mnu lists -sh-mnu (nf_venue :247) and no -fh-mnu row was
        read: his 'Manchester United FC leading at halftime?' refuses."""
        rows = _stored(_board())
        assert _resolve(rows, "ht-mun") is None
        ex = _explain(rows, "ht-mun")
        assert ex["split"] == "halftime:subject-unwitnessed"
        assert ex["yn_c5"]["lane"]["venue_subjects"] == ["everton fc"]
        # no -fh- per-team row at all on the event: no-row, never -sh-
        rows = _stored(_board(drop={f"{E}-fh-eve"}))
        assert _resolve(rows, "ht-mun") is None
        assert _explain(rows, "ht-mun")["split"] == "halftime:no-row"
        # and the full-game moneyline row is never this lane's
        ids = {r["identifier"] for rs in premap._c6_family_rows(
            rows, premap.c6_his(f"epl-eve-mnu-{D}-halftime-result-away")).values() for r in rs}
        assert ids == {f"{E}-fh-draw"}

    def test_the_halftime_exact_score_twin_is_never_the_full_games(self, armed):
        rows = _stored(_board(drop={f"{E}-exact-score-1-2"}))
        assert [r for r in rows if r["identifier"] == f"{E}-fh-exact-score-1-2"]
        assert _resolve(rows, "es-1-2") is None
        assert _explain(rows, "es-1-2")["split"] == "exact:no-row"
        # nor is any exact-score row the halftime lane's
        ids = {r["identifier"] for rs in premap._c6_family_rows(
            rows, premap.c6_his(f"epl-ars-cfc-{D}-halftime-result-away")).values() for r in rs}
        assert ids == {f"{AC}-fh-ars", f"{AC}-fh-cfc", f"{AC}-fh-draw"}

    def test_his_title_shapes(self, armed):
        rows = _stored(_board())
        for bad in ("Chelsea FC to lead at halftime?", "Will Chelsea FC lead at halftime?",
                    "Chelsea FC leading at halftime? (Aggregate)", "Chelsea FC leading?"):
            assert _resolve(rows, "ht-away", title=bad) is None
            assert _explain(rows, "ht-away", title=bad)["split"] == "halftime:title-shape"
        # no event title: his one-club title builds no matchup key (a
        # lookup fact, as C5's), and the lane itself reads no other club
        assert _resolve(rows, "ht-away", ev=None) is None
        assert _explain(rows, "ht-away", ev=None)["step"] == "no_key_intersection"
        assert _pick(rows, "ht-away", ev=None)[1]["refusal"] == "halftime:event-unwitnessed"
        # his title's club is not a side of his event: shear
        assert _pick(rows, "ht-away", ev="Everton FC vs. Manchester United FC")[1]["refusal"] == \
            "halftime:event-shear"

    def test_la_liga_home_on_the_verbatim_esp_row(self, armed):
        rows = _stored(_board())
        h = asyncio.run(premap.resolve(_Pool(rows), "RCD Espanyol de Barcelona leading at halftime?",
                                       ESP_SEV, "Yes", f"lal-esp-sev-{D}-halftime-result-home"))
        assert _short(h) == (f"atc-lal-esp-sev-{D}-fh-esp", "yes", LONG, "premap_halftime")
        assert h["title"] == "Will RCD Espanyol de Barcelona lead Sevilla FC at halftime?"
        # the slug's other token on the same title: the same row
        h = asyncio.run(premap.resolve(_Pool(rows), "RCD Espanyol de Barcelona leading at halftime?",
                                       ESP_SEV, "Yes", f"lal-esp-sev-{D}-halftime-result-away"))
        assert h["market_slug"] == f"atc-lal-esp-sev-{D}-fh-esp"


# ------------------------------------------------- the census, purity, the mirror

class TestTheCensusAndTheSeams:
    def test_the_refusal_names_are_closed(self):
        src = inspect.getsource(premap._c6_pick_exact) + inspect.getsource(premap._c6_pick_halftime) \
            + inspect.getsource(premap.c6_pick) + inspect.getsource(premap._c6_side_row) \
            + inspect.getsource(premap._c6_code)
        import re
        names = set(re.findall(r'"((?:exact|halftime|\{tag\}):[a-z-]+)"', src))
        assert names == {
            "exact:outcome", "exact:title-shape", "exact:title-score", "exact:question-shape",
            "exact:codes", "exact:other", "exact:order-uncertified", "exact:club-unwitnessed",
            "halftime:outcome", "halftime:title-shape", "halftime:event-unwitnessed",
            "halftime:event-shear", "halftime:question-shape", "halftime:codes",
            "halftime:subject-unwitnessed", "halftime:no-row", "halftime:ambiguous",
            "{tag}:slug-shape", "{tag}:league-ambiguous", "{tag}:no-row", "{tag}:side",
            "{tag}:ambiguous", "{tag}:intent",
        }
        assert premap.C3_ABSENT["exact_score"] == "exact:family-absent"
        assert premap.C3_ABSENT["halftime_result"] == "halftime:family-absent"

    def test_the_pick_is_pure(self):
        for fn in (premap.c6_pick, premap._c6_pick_exact, premap._c6_pick_halftime, premap.c6_his,
                   premap._c6_family_rows, premap._c6_clubs_witnessed):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv"):
                assert forbidden not in src, (fn.__name__, forbidden)

    def test_a_league_ambiguity_refuses(self, armed):
        twin = {f"epw-eve-mnu-{D}": (EVE_MNU, _game(
            "epw", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League",
            fh=("eve", "mnu")))}
        rows = _stored(_board({k: v for k, v in VENUE.items() if "eve-mnu" not in k}, twin))
        rows += _stored(_board({f"epx-eve-mnu-{D}": (EVE_MNU, _game(
            "epx", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League",
            fh=("eve", "mnu")))}))
        slug = f"xyz-eve-mnu-{D}-exact-score-2-1"
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), FEED["es-2-1"][1], EVE_MNU, "Yes", slug))
        assert ex["split"] == "exact:league-ambiguous" and ex["c6"]["league_codes"] == ["epw", "epx"]
        assert asyncio.run(premap.resolve(_Pool(rows), FEED["es-2-1"][1], EVE_MNU, "Yes", slug)) is None

    def test_the_mirror_maps_them_as_premap(self, armed):
        rows = _stored(_board())
        for key, want in (("es-2-1", f"{E}-exact-score-2-1"), ("ht-away", f"{AC}-fh-cfc")):
            slug, title, ev, oc = FEED[key]
            fills = [{"asset": "tok-a", "market_slug": slug, "market_title": title,
                      "event_title": ev, "outcome": oc, "outcome_index": 0, "side": "BUY",
                      "size": 10.0, "price": 0.5}]
            m = asyncio.run(ms.map_market(_Pool(rows), fills))
            assert m and m["source"] == "premap" and m["us_slug"] == want, key
        from sportsassets import copy_sports as cs
        assert cs.mirror_family_of(FEED["es-2-1"][0]) == "exact_score"
        assert ms._family_of(FEED["es-2-1"][0]) == "exact_score"
