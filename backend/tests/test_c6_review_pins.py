"""C6 review pins (adversarial review of the M2 lane, 2026-09-07).

The fixture is the builder's (test_c6_exact_halftime: the verbatim rows
of epl_rows_1338.log:241-314 and the -fh- rows of nf_venue_1350.log);
these pins hold the mandate's edges the builder's file does not:

  * the digit ORDER is certified by the venue's question ALONE -- every
    (winner, m, n) / (draw d, d') / 'other' wording against every X-Y
    identifier, exactly one maps; the codes reversed refuse; X==Y with
    'wins' refuses;
  * the row placed on is the row whose words were read (side / intent /
    duplicate rows);
  * no other arm -- the wording arm, the yes/no identity branch, the
    named lane, a moneyline or a draw slug -- lands on an exact-score
    row now that its phantom line is gone, switch on or off;
  * the halftime lane never trades a row whose identifier and question
    disagree (the C5 'rows-swapped' shape), and reads the question of
    the row it places on.

Two pins are marked xfail(strict=True): they are the review's findings
(M2_review.md), and turn green only once the lane is fixed.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest

from sportsassets.workers import premap
from tests.test_c6_exact_halftime import (
    AC, D, E, EVE_MNU, FEED, LONG, SHORT, VENUE, _board, _explain, _game, _Pool, _resolve,
    _reword, _short, _stored,
)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _title(x: int, y: int) -> str:
    return f"Exact Score: Everton FC {x} - {y} Manchester United FC?"


def _slug(x: int, y: int) -> str:
    return f"epl-eve-mun-{D}-exact-score-{x}-{y}"


def _canonical(x: int, y: int) -> str:
    if x > y:
        return f"Will EVE vs MNU finish EVE wins {x}-{y}?"
    if x < y:
        return f"Will EVE vs MNU finish MNU wins {y}-{x}?"
    return f"Will EVE vs MNU finish Draw {x}-{x}?"


def _wordings() -> list[str]:
    out = [f"Will EVE vs MNU finish {w} wins {m}-{n}?"
           for w in ("EVE", "MNU") for m in range(4) for n in range(4)]
    out += [f"Will EVE vs MNU finish Draw {m}-{n}?" for m in range(4) for n in range(4)]
    out.append("Will EVE vs MNU finish Other?")
    return out


# ------------------------------------------------ the order, exhaustively

class TestTheOrderIsCertifiedByTheQuestionAlone:
    @pytest.mark.parametrize("x,y", [(x, y) for x in range(4) for y in range(4)])
    def test_exactly_one_wording_maps_the_x_y_identifier(self, armed, x, y):
        base = _stored(_board())
        ident = f"{E}-exact-score-{x}-{y}"
        if not any(r["identifier"] == ident for r in base):
            # 3-3 is not listed by the venue (SCORES); give it the row
            base = _stored(_board(extra={f"x-{D}": ("", [
                {"slug": ident, "question": _canonical(x, y), "marketSides": [
                    {"identifier": ident, "description": "Yes", "long": True},
                    {"identifier": ident, "description": "No", "long": False}]}])}))
            for r in base:
                if r["identifier"] == ident:
                    r["event_keys"] = premap.event_keys_for(EVE_MNU, f"epl-eve-mnu-{D}")
        mapped = []
        for q in _wordings():
            rows = _reword(base, ident, q)
            for oc, side, intent in (("Yes", "yes", LONG), ("No", "no", SHORT)):
                h = asyncio.run(premap.resolve(_Pool(rows), _title(x, y), EVE_MNU, oc, _slug(x, y)))
                if h is not None:
                    assert _short(h) == (ident, side, intent, "premap_exact_score"), (q, oc)
                    mapped.append(q)
                else:
                    ex = asyncio.run(premap.resolve_explain(_Pool(rows), _title(x, y), EVE_MNU,
                                                            oc, _slug(x, y)))
                    assert ex["split"] in ("exact:order-uncertified", "exact:other"), (q, oc, ex)
        assert mapped == [_canonical(x, y)] * 2

    @pytest.mark.parametrize("x,y", [(2, 1), (1, 2), (1, 1), (0, 0), (3, 0)])
    def test_the_codes_reversed_refuse_on_every_shape(self, armed, x, y):
        ident = f"{E}-exact-score-{x}-{y}"
        q = _canonical(x, y).replace("EVE vs MNU", "MNU vs EVE")
        rows = _reword(_stored(_board()), ident, q)
        assert asyncio.run(premap.resolve(_Pool(rows), _title(x, y), EVE_MNU, "Yes",
                                          _slug(x, y))) is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), _title(x, y), EVE_MNU, "Yes",
                                                _slug(x, y)))
        assert ex["split"] == "exact:codes"

    def test_his_own_digits_must_be_his_titles(self, armed):
        # slug -1-2 with the title 'Everton FC 2 - 1 …': the feed's own
        # words disagree; nothing is inferred from either
        rows = _stored(_board())
        h = asyncio.run(premap.resolve(_Pool(rows), _title(2, 1), EVE_MNU, "Yes", _slug(1, 2)))
        assert h is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), _title(2, 1), EVE_MNU, "Yes",
                                                _slug(1, 2)))
        assert ex["split"] == "exact:title-score"

    def test_the_venues_p4_rows_swapped_refuse_the_score(self, armed):
        # the venue's -eve row asks about Manchester United and its -mnu
        # row about Everton: the club witness refuses (subject:eve)
        rows = _stored(_board())
        rows = _reword(rows, f"{E}-eve", "Will Manchester United FC win against Everton FC in "
                                          "the Premier League match scheduled for Sep 6, 2026?")
        rows = _reword(rows, f"{E}-mnu", "Will Everton FC win against Manchester United FC in "
                                          "the Premier League match scheduled for Sep 6, 2026?")
        assert _resolve(rows, "es-2-1") is None
        ex = _explain(rows, "es-2-1")
        assert ex["split"] == "exact:club-unwitnessed"
        assert ex["yn_c5"]["lane"]["why"] == "subject:eve"


# ------------------------------------------------ the row placed on

class TestTheRowPlacedOnIsTheRowRead:
    def test_a_row_carrying_the_other_intent_refuses(self, armed):
        rows = _stored(_board())
        rows = [dict(r, intent=SHORT) if r["identifier"] == f"{E}-exact-score-2-1"
                and r["side_norm"] == "yes" else r for r in rows]
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["split"] == "exact:intent"
        rows = _stored(_board())
        rows = [dict(r, intent=SHORT) if r["identifier"] == f"{AC}-fh-cfc"
                and r["side_norm"] == "yes" else r for r in rows]
        assert _resolve(rows, "ht-away") is None
        assert _explain(rows, "ht-away")["split"] == "halftime:intent"

    def test_two_rows_on_his_side_refuse(self, armed):
        rows = _stored(_board())
        dup = [dict(r) for r in rows if r["identifier"] == f"{E}-exact-score-2-1"
               and r["side_norm"] == "yes"]
        assert _resolve(rows + dup, "es-2-1") is None
        assert _explain(rows + dup, "es-2-1")["split"] == "exact:ambiguous"
        dup = [dict(r) for r in rows if r["identifier"] == f"{AC}-fh-cfc" and r["side_norm"] == "yes"]
        assert _resolve(rows + dup, "ht-away") is None
        assert _explain(rows + dup, "ht-away")["split"] == "halftime:ambiguous"

    def test_the_side_missing_refuses(self, armed):
        rows = [r for r in _stored(_board())
                if not (r["identifier"] == f"{E}-exact-score-2-1" and r["side_norm"] == "yes")]
        assert _resolve(rows, "es-2-1") is None
        assert _explain(rows, "es-2-1")["split"] == "exact:side"

    def test_the_exact_lane_reads_the_question_of_the_row_it_places_on(self, armed):
        # only the NO row of -1-2 is reworded: his No refuses, his Yes maps
        rows = [dict(r, question="Will EVE vs MNU finish EVE wins 2-1?")
                if r["identifier"] == f"{E}-exact-score-1-2" and r["side_norm"] == "no" else r
                for r in _stored(_board())]
        assert _resolve(rows, "es-1-2") is None
        assert _explain(rows, "es-1-2")["split"] == "exact:order-uncertified"
        h = _resolve(rows, "es-1-2", oc="Yes")
        assert _short(h) == (f"{E}-exact-score-1-2", "yes", LONG, "premap_exact_score")

    def test_the_halftime_lane_reads_the_question_of_the_row_it_places_on(self, armed):
        # (M2 review LOW-1, folded in v2) the -fh-cfc NO row alone reworded
        # as Arsenal leading: his No must refuse (the row he would be placed
        # on names the other club); his Yes still maps on the row that
        # names Chelsea
        rows = [dict(r, question="Will Arsenal FC lead Chelsea FC at halftime?")
                if r["identifier"] == f"{AC}-fh-cfc" and r["side_norm"] == "no" else r
                for r in _stored(_board())]
        assert _resolve(rows, "ht-away-no") is None
        ex = _explain(rows, "ht-away-no")
        assert ex["split"] == "halftime:subject-unwitnessed"
        assert ex["yn_c5"]["lane"]["why"] == "side-row-question"
        assert _short(_resolve(rows, "ht-away")) == (f"{AC}-fh-cfc", "yes", LONG, "premap_halftime")


# ------------------------------------------------ the halftime identifier vs its question

class TestTheHalftimeIdentifierAndItsQuestionMustAgree:
    def test_venue_rows_swapped_refuse(self, armed):
        # (M2 review MEDIUM-1, folded in v2) -fh-ars worded as Chelsea
        # leading and -fh-cfc as Arsenal leading (the C5 'rows-swapped'
        # shape): the identifier says one club, the question the other --
        # nothing of the venue's certifies which
        rows = _reword(_stored(_board()), f"{AC}-fh-ars", "Will Chelsea FC lead Arsenal FC at halftime?")
        rows = _reword(rows, f"{AC}-fh-cfc", "Will Arsenal FC lead Chelsea FC at halftime?")
        assert _resolve(rows, "ht-away") is None
        assert _resolve(rows, "ht-home") is None

    def test_the_swapped_rows_refuse_by_name_where_v1_traded_the_question(self, armed):
        """The change of verdict MEDIUM-1 asked for, recorded: v1 landed
        his 'Chelsea FC leading' on -fh-ars (the row whose question named
        Chelsea); v2 holds the identifier's code against the P4 witness
        and refuses with the code named."""
        rows = _reword(_stored(_board()), f"{AC}-fh-ars", "Will Chelsea FC lead Arsenal FC at halftime?")
        rows = _reword(rows, f"{AC}-fh-cfc", "Will Arsenal FC lead Chelsea FC at halftime?")
        ex = _explain(rows, "ht-away")
        assert ex["step"] == "no_side_match" and ex["split"] == "halftime:subject-unwitnessed"
        assert ex["yn_c5"]["lane"]["why"] == "code-shear:ars"
        assert ex["yn_c5"]["lane"]["p4"] == "subject:ars"
        ex = _explain(rows, "ht-home")
        assert ex["yn_c5"]["lane"]["why"] == "code-shear:cfc"

    def test_a_p4_row_missing_leaves_the_identifiers_code_unwitnessed(self, armed):
        # the -fh-cfc question names Chelsea, but without the event's -cfc
        # P4 row nothing binds the code to the club
        rows = _stored(_board(drop={f"{AC}-cfc"}))
        assert _resolve(rows, "ht-away") is None
        # (his -che slug cannot be translated without the -cfc row: C5
        # never certifies, so the untranslated lane's family-absent stands)
        ex = _explain(rows, "ht-away")
        assert (ex["step"], ex["refusal"]) == ("unknown_market_type", "halftime:family-absent")
        venue = {f"epl-ars-che-{D}": ("Arsenal FC vs. Chelsea FC", _game(
            "epl", "ars", "che", "Arsenal FC", "Chelsea FC", "Premier League", fh=("ars", "che")))}
        rows = _stored(_board(venue, drop={f"atc-epl-ars-che-{D}-che"}))
        assert _resolve(rows, "ht-away") is None
        ex = _explain(rows, "ht-away")
        assert ex["c6"]["why"] == "code-shear:che" and ex["c6"]["p4"] == "p4-row-absent:che"


# ------------------------------------------------ no other arm lands on these rows

class TestNoOtherArmLandsOnAnExactScoreRow:
    @pytest.mark.parametrize("switch", ["armed", "dark"])
    def test_a_moneyline_and_a_draw_slug_on_the_full_board(self, monkeypatch, switch):
        if switch == "armed":
            monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        else:
            monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        # his codes ARE the venue's (no C5 needed either way)
        venue = {f"epl-eve-mnu-{D}": (EVE_MNU, _game(
            "epl", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League",
            fh=("eve", "mnu"), fh_exact=("0-0", "1-1"), sh=True))}
        for rows in (_stored(_board(venue)), _board(venue)):
            # the stored rows (phantom line) and the freshly swept rows (no
            # line) answer the same on every slug
            for slug, title, oc, want in (
                    (f"epl-eve-mnu-{D}-mnu", "Will Manchester United FC win on 2026-09-06?", "Yes",
                     f"atc-epl-eve-mnu-{D}-mnu"),
                    (f"epl-eve-mnu-{D}-eve", "Will Everton FC win on 2026-09-06?", "No",
                     f"atc-epl-eve-mnu-{D}-eve"),
                    (f"epl-eve-mnu-{D}-draw", "Will Everton FC vs. Manchester United FC end in a draw?",
                     "Yes", f"atc-epl-eve-mnu-{D}-draw")):
                h = asyncio.run(premap.resolve(_Pool(rows), title, EVE_MNU, oc, slug))
                if switch == "armed":
                    assert h is not None and h["market_slug"] == want, (slug, switch)
                else:
                    # dark: the dated wording arm alone; whatever it answers
                    # it is never an exact-score / -fh- / -sh- row
                    assert h is None or h["market_slug"] == want, (slug, switch)
                # a bare 'Yes' with a title of another market on the event
                for t in ("Will EVE vs MNU finish Draw 0-0?", "Will EVE vs MNU finish EVE wins 2-1?",
                          "Will EVE vs MNU be tied at halftime?"):
                    h = asyncio.run(premap.resolve(_Pool(rows), t, EVE_MNU, "Yes", slug))
                    assert h is None, (slug, t, switch)

    def test_the_wording_arm_never_sees_a_c6_slug(self, armed, monkeypatch):
        seen = []
        real = premap.match_side

        def spy(rows, outcome, his_title, his_slug=None, **kw):
            seen.append(his_slug)
            return real(rows, outcome, his_title, his_slug, **kw)

        monkeypatch.setattr(premap, "match_side", spy)
        rows = _stored(_board())
        assert _resolve(rows, "es-2-1") is not None
        assert _resolve(rows, "ht-away") is not None
        assert _resolve(rows, "ht-draw") is not None
        _explain(rows, "es-2-1")
        _explain(rows, "ht-away")
        assert seen == []

    def test_the_named_lane_on_changes_nothing(self, armed, monkeypatch):
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")
        rows = _stored(_board())
        assert _short(_resolve(rows, "es-2-1"))[0] == f"{E}-exact-score-2-1"
        assert _short(_resolve(rows, "ht-away"))[0] == f"{AC}-fh-cfc"
        # a named outcome on a C6 slug is not a C6 pick and not a named pick
        h = asyncio.run(premap.resolve(_Pool(rows), FEED["es-2-1"][1], EVE_MNU, "Everton FC",
                                       FEED["es-2-1"][0]))
        assert h is None
        assert _explain(rows, "es-2-1", oc="Everton FC")["split"] == "exact:outcome"

    def test_dark_a_moneyline_answers_the_same_on_lined_and_unlined_rows(self, dark):
        venue = {f"epl-eve-mnu-{D}": (EVE_MNU, _game(
            "epl", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League"))}
        a = _stored(_board(venue))
        b = _board(venue)
        for slug, title, oc in ((f"epl-eve-mnu-{D}-mnu", "Will Manchester United FC win on 2026-09-06?", "Yes"),
                                (f"epl-eve-mnu-{D}-mnu", "Will Manchester United FC win?", "No"),
                                (f"epl-eve-mnu-{D}-total-2pt5", f"{EVE_MNU}: O/U 2.5", "Over")):
            ha = asyncio.run(premap.resolve(_Pool(a), title, EVE_MNU, oc, slug))
            hb = asyncio.run(premap.resolve(_Pool(b), title, EVE_MNU, oc, slug))
            assert (ha or {}).get("market_slug") == (hb or {}).get("market_slug"), slug
            ea = asyncio.run(premap.resolve_explain(_Pool(a), title, EVE_MNU, oc, slug))
            eb = asyncio.run(premap.resolve_explain(_Pool(b), title, EVE_MNU, oc, slug))
            assert (ea["step"], ea.get("split")) == (eb["step"], eb.get("split")), slug


# ------------------------------------------------ segments and twins

class TestSegmentsNeverCross:
    def test_only_the_halftime_twins_listed_is_the_family_absent(self, armed):
        rows = _stored(_board(drop={f"{E}-exact-score-{s}" for s in
                                    ["0-0", "0-1", "0-2", "0-3", "1-0", "1-1", "1-2", "1-3", "2-0",
                                     "2-1", "2-2", "2-3", "3-0", "3-1", "3-2", "other"]}))
        assert [r for r in rows if r["identifier"] == f"{E}-fh-exact-score-1-2"]
        assert _resolve(rows, "es-1-2") is None
        ex = _explain(rows, "es-1-2")
        assert (ex["step"], ex["split"], ex["refusal"]) == \
            ("unknown_market_type", "family_not_listed", "exact:family-absent")

    def test_a_halftime_exact_score_row_is_never_the_halftime_results(self, armed):
        # his '-halftime-result-draw' against an event listing only the
        # -fh-exact-score-1-1 'Draw 1-1 at halftime' twin and no -fh-draw
        venue = {f"epl-eve-mnu-{D}": (EVE_MNU, _game(
            "epl", "eve", "mnu", "Everton FC", "Manchester United FC", "Premier League",
            fh_exact=("0-0", "1-1"), sh=True))}
        rows = _stored(_board(venue))
        t = "Everton FC vs. Manchester United FC: Draw at halftime?"
        h = asyncio.run(premap.resolve(_Pool(rows), t, EVE_MNU, "Yes", f"epl-eve-mnu-{D}-halftime-result-draw"))
        assert h is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), t, EVE_MNU, "Yes",
                                                f"epl-eve-mnu-{D}-halftime-result-draw"))
        assert ex["refusal"] == "halftime:family-absent"

    def test_the_family_rows_carry_his_date_and_his_pairing_only(self, armed):
        # m18: the family-row filter itself, not only the identifier the
        # lane builds from it, keeps another date's and the reversed
        # pairing's rows out
        rows = _stored(_board())
        for ident, q in ((f"atc-epl-eve-mnu-2026-09-13-exact-score-2-1", "Will EVE vs MNU finish EVE wins 2-1?"),
                         (f"atc-epl-mnu-eve-{D}-exact-score-1-2", "Will MNU vs EVE finish EVE wins 2-1?"),
                         (f"atc-epl-eve-mnu-2026-09-13-fh-eve", "Will Everton FC lead Manchester United FC at halftime?")):
            rows += premap._market_rows({"slug": f"epl-eve-mnu-{D}", "title": EVE_MNU},
                                        {"slug": ident, "question": q, "marketSides": [
                                            {"identifier": ident, "description": "Yes", "long": True},
                                            {"identifier": ident, "description": "No", "long": False}]})
        for slug in (f"epl-eve-mnu-{D}-exact-score-2-1", f"epl-eve-mnu-{D}-halftime-result-home"):
            ids = {r["identifier"] for rs in premap._c6_family_rows(rows, premap.c6_his(slug)).values()
                   for r in rs}
            assert all(i.startswith(f"atc-epl-eve-mnu-{D}-") for i in ids), (slug, ids)
            assert ids, slug

    def test_the_halftime_subject_must_be_his_club_not_only_the_opponent(self, armed):
        # m22: the -fh-cfc question names his OTHER club as the opponent
        # but a third club as the leader -- the opponent alone is no witness
        rows = _reword(_stored(_board()), f"{AC}-fh-cfc", "Will Everton FC lead Arsenal FC at halftime?")
        assert _resolve(rows, "ht-away") is None
        ex = _explain(rows, "ht-away")
        assert ex["split"] == "halftime:subject-unwitnessed"
        assert ex["yn_c5"]["lane"]["venue_subjects"] == ["arsenal fc", "everton fc"]

    def test_another_date_and_another_pairing_never_serve(self, armed):
        # the same codes on another date, and the codes reversed on his
        # date, both on the board under his event keys
        rows = _stored(_board(drop={f"{E}-exact-score-2-1"}))
        extra = []
        for ident, q in ((f"atc-epl-eve-mnu-2026-09-13-exact-score-2-1", "Will EVE vs MNU finish EVE wins 2-1?"),
                         (f"atc-epl-mnu-eve-{D}-exact-score-1-2", "Will MNU vs EVE finish EVE wins 2-1?")):
            for r in premap._market_rows({"slug": f"epl-eve-mnu-{D}", "title": EVE_MNU},
                                         {"slug": ident, "question": q, "marketSides": [
                                             {"identifier": ident, "description": "Yes", "long": True},
                                             {"identifier": ident, "description": "No", "long": False}]}):
                r["event_keys"] = premap.event_keys_for(EVE_MNU, f"epl-eve-mnu-{D}")
                extra.append(r)
        assert _resolve(rows + extra, "es-2-1") is None
        assert _explain(rows + extra, "es-2-1")["split"] == "exact:no-row"
