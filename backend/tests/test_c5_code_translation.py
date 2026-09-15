"""C5 (2026-09-07): a differing TEAM CODE is translated by the venue's own
question, per game -- never a table, never a code-similarity.

The venue's rows for the two Premier League games he traded on
2026-09-06 (us_premap, preset epl-rows, read 2026-09-07 00:04Z --
scratchpad/hard2/epl_rows_0004.log) are reproduced below verbatim:
identifier, sides, intents, question. His feed names Manchester United
`mun` and Chelsea `che`; the venue names them `mnu` and `cfc`, under the
same league code (`epl`), the same date and the same OTHER club code
(`eve`, `ars`). The identity lane (C2) reads `yn:no-row` on every one of
his markets on those games because the identifier never matches; this
build certifies `mun -> mnu` / `che -> cfc` FOR THAT EVENT from the
venue's per-team question naming his club as his own title states it,
then runs the identity lane exactly as today on the rewritten slug.

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
# the venue's rows, verbatim (epl_rows_0004.log:241-332, 339-362, 375-396).
# The us_premap `event_title` column was not printed by the preset; the
# venue's event title below follows the naming its questions carry, as
# the C2/C3 fixtures did for their dumps (an ASSUMPTION about the lookup
# key, never about a decision: every decision below reads the question).

EVE_MNU = "Everton FC vs. Manchester United FC"
ARS_CFC = "Arsenal FC vs. Chelsea FC"

P4 = "Will {a} win against {b} in the Premier League match scheduled for Sep 6, 2026?"
DRAWQ = "Will the Premier League match {a} vs {b} scheduled for Sep 6, 2026 end in a draw?"


def _yn(ident: str, q: str) -> dict:
    """The venue's own side expansion: both sides share the identifier;
    `long` names the intent (the dump's intents)."""
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _tsc(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Over", "long": True},
        {"identifier": ident, "description": "Under", "long": False}]}


def _asc(ident: str, q: str) -> dict:
    line = ident.rsplit("-", 1)[-1].replace("pt", ".")
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": line, "long": True},
        {"identifier": ident, "description": line, "long": False}]}


def _game(lg: str, va: str, vb: str, A: str, B: str, codes: str, clock: str) -> list[dict]:
    """Every row family the epl-rows preset printed for one game."""
    e = f"{lg}-{va}-{vb}-{D}"
    out = [
        _yn(f"atc-{e}-{va}", P4.format(a=A, b=B)),
        _yn(f"atc-{e}-{vb}", P4.format(a=B, b=A)),
        _yn(f"atc-{e}-draw", DRAWQ.format(a=A, b=B)),
        _yn(f"atc-{e}-exact-score-0-0", f"Will {codes} finish Draw 0-0?"),
        _yn(f"atc-{e}-exact-score-2-1", f"Will {codes} finish {va.upper()} wins 2-1?"),
        _yn(f"atc-{e}-exact-score-other", f"Will {codes} finish Other?"),
        _yn(f"astatc-{e}-btts",
            f"Will both teams score in the match between {A} and {B} on {D} {clock} ET?"),
        _yn(f"astatc-{e}-fh-btts",
            f"Will both teams score in the first half between {A} and {B} on {D} {clock} ET?"),
    ]
    for ln in ("0pt5", "1pt5", "2pt5", "3pt5"):
        out.append(_tsc(f"tsc-{e}-{ln}",
                        f"Will the total in {codes} be more than {ln.replace('pt', '.')}?"))
    for seg in ("", "-fh", "-sh"):
        for ln in ("1pt5", "2pt5"):
            L = ln.replace("pt", ".")
            out.append(_asc(f"asc-{e}{seg}-neg-{ln}",
                            f"Will the {A} cover -{L} vs the {B} in {codes}?"))
            out.append(_asc(f"asc-{e}{seg}-pos-{ln}",
                            f"Will the {A} cover {L} vs the {B} in {codes}?"))
    return out


# the two games, verbatim; the venue's event slug is the dump's
# `event_slug` column (kindless)
VENUE = {
    f"epl-eve-mnu-{D}": (EVE_MNU, _game("epl", "eve", "mnu", "Everton FC",
                                        "Manchester United FC", "EVE vs MNU", "9:00AM")),
    f"epl-ars-cfc-{D}": (ARS_CFC, _game("epl", "ars", "cfc", "Arsenal FC",
                                        "Chelsea FC", "ARS vs CFC", "11:30AM")),
}

# rows the census attests by identifier only (epl_rows_0004.log:157-158
# name `atc-epl-eve-mnu-2026-09-06-sh-mnu` and `tsc-…-sh-4pt5` as the
# league's max identifiers): the per-team HALF rows, worded as the C2
# dump words them -- an EXPECTATION about the wording, pinned only so a
# half row is proven never to be a candidate or a witness
VENUE_HALVES = {
    f"epl-eve-mnu-{D}": (EVE_MNU, [
        _yn(f"atc-epl-eve-mnu-{D}-fh-mnu", "Will Manchester United FC lead Everton FC at halftime?"),
        _yn(f"atc-epl-eve-mnu-{D}-sh-mnu",
            "Will Manchester United FC win the second half against Everton FC?"),
        _tsc(f"tsc-epl-eve-mnu-{D}-fh-1pt5", "Will the total in EVE vs MNU be more than 1.5?"),
        _tsc(f"tsc-epl-eve-mnu-{D}-sh-4pt5", "Will the total in EVE vs MNU be more than 4.5?"),
    ]),
}

# his feed: (market_slug, market_title, markets.event_title, outcome).
# The moneyline title is the brief's verbatim row; the draw / total /
# btts / spread titles are the feed's attested wordings (C3 §9.1).
FEED = {
    "mun-yes": (f"epl-eve-mun-{D}-mun", "Will Manchester United FC win on 2026-09-06?",
                EVE_MNU, "Yes"),
    "mun-no": (f"epl-eve-mun-{D}-mun", "Will Manchester United FC win on 2026-09-06?",
               EVE_MNU, "No"),
    "eve-yes": (f"epl-eve-mun-{D}-eve", "Will Everton FC win on 2026-09-06?", EVE_MNU, "Yes"),
    "draw": (f"epl-eve-mun-{D}-draw", "Will Everton FC vs. Manchester United FC end in a draw?",
             EVE_MNU, "Yes"),
    "total": (f"epl-eve-mun-{D}-total-2pt5", "Everton FC vs. Manchester United FC: O/U 2.5",
              EVE_MNU, "Under"),
    "total-over": (f"epl-eve-mun-{D}-total-3pt5", "Everton FC vs. Manchester United FC: O/U 3.5",
                   EVE_MNU, "Over"),
    "fh-total": (f"epl-eve-mun-{D}-first-half-total-1pt5",
                 "Everton FC vs. Manchester United FC: 1st Half O/U 1.5", EVE_MNU, "Under"),
    "btts": (f"epl-eve-mun-{D}-btts", "Everton FC vs. Manchester United FC: Both Teams to Score",
             EVE_MNU, "Yes"),
    "spread": (f"epl-eve-mun-{D}-spread-away-1pt5", "Spread: Manchester United FC (-1.5)",
               EVE_MNU, "Manchester United FC"),
    "che-yes": (f"epl-ars-che-{D}-che", "Will Chelsea FC win on 2026-09-06?", ARS_CFC, "Yes"),
    "che-no": (f"epl-ars-che-{D}-che", "Will Chelsea FC win on 2026-09-06?", ARS_CFC, "No"),
    "ars-yes": (f"epl-ars-che-{D}-ars", "Will Arsenal FC win on 2026-09-06?", ARS_CFC, "Yes"),
    "ars-draw": (f"epl-ars-che-{D}-draw", "Will Arsenal FC vs. Chelsea FC end in a draw?",
                 ARS_CFC, "Yes"),
    "ars-btts": (f"epl-ars-che-{D}-btts", "Arsenal FC vs. Chelsea FC: Both Teams to Score",
                 ARS_CFC, "No"),
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


class _Pool:
    """us_premap by key intersection; `trades`/`markets` titles by slug
    (the witness read C5 makes when his own title names no club)."""

    def __init__(self, rows, titles: dict[str, list[str]] | None = None):
        self.rows = rows
        self.titles = titles or {}
        self.reads: list[str] = []

    async def fetch(self, sql, *a):
        if "us_premap" in sql:
            self.reads.append("us_premap")
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        assert "trades" in sql and "markets" in sql, sql
        assert a[1] == a[0].rsplit("-", 1)[0], a       # (market slug, his event slug)
        self.reads.append("titles")
        return [{"t": t} for t in self.titles.get(a[0], [])]


def _resolve(rows, key, *, ev="keep", titles=None, pool=None):
    slug, title, evt, oc = FEED[key]
    p = pool or _Pool(rows, titles)
    return asyncio.run(premap.resolve(p, title, evt if ev == "keep" else ev, oc, slug))


def _explain(rows, key, *, ev="keep", titles=None, pool=None):
    slug, title, evt, oc = FEED[key]
    p = pool or _Pool(rows, titles)
    return asyncio.run(premap.resolve_explain(p, title, evt if ev == "keep" else ev, oc, slug))


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


def _with_question(rows, ident, q):
    return [dict(r, question=q) if r["identifier"] == ident else dict(r) for r in rows]


MNU = f"atc-epl-eve-mnu-{D}-mnu"
EVE = f"atc-epl-eve-mnu-{D}-eve"
DRAW = f"atc-epl-eve-mnu-{D}-draw"
CFC = f"atc-epl-ars-cfc-{D}-cfc"
ARS = f"atc-epl-ars-cfc-{D}-ars"
S_MUN, T_MUN = FEED["mun-yes"][0], FEED["mun-yes"][1]
T_CHE = FEED["che-yes"][1]
AMBIG, UNWIT, MISMATCH = ("yn:code-translate:ambiguous", "yn:code-translate:unwitnessed",
                          "yn:code-translate:name-mismatch")
# his moneyline titles on the unshared clubs as trades.market_title stores them
HIS_ML = {f"epl-eve-mun-{D}-mun": [T_MUN], f"epl-ars-che-{D}-che": [T_CHE]}


def _certify(rows, title, ev, slug, titles=None):
    tr: dict = {}
    cert = asyncio.run(premap.c5_certify(_Pool(rows, titles), rows, title, ev, slug, tr))
    return cert, tr


# ------------------------------------------- (a) (b) (d): the moneylines

class TestAMoneylineTranslatesByTheVenuesQuestion:
    def test_a_his_buy_of_manchester_united_lands_on_the_venues_mnu_row(self, armed):
        rows = _board()
        p = _Pool(rows)
        h = _resolve(rows, "mun-yes", pool=p)
        assert _short(h) == (MNU, "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        assert "league_alias" not in h
        # his own title is the witness: no stored title is read
        assert p.reads == ["us_premap"]
        assert _short(_resolve(rows, "mun-no")) == (MNU, "no", SHORT, "premap_identity")
        ex = _explain(rows, "mun-yes")
        assert ex["step"] == "resolves" and ex["detail"] == MNU
        assert ex["matched_by"] == "premap_identity"
        assert ex["code_translated"] == {"mun": "mnu"} and ex["witness"] == MNU
        c5 = ex["yn_c5"]
        assert c5["label"] == "yn:code-translated" and c5["witness_src"] == "title"
        assert c5["his_club"] == "manchester united fc" == c5["venue_club"]
        assert c5["event"] == f"epl-eve-mnu-{D}" and c5["shared"] == "eve"
        assert c5["pair"] == ["mun", "mnu"] and c5["slug"] == f"epl-eve-mnu-{D}-mnu"
        assert c5["admitted"] == MNU

    def test_d_chelsea_translates_to_cfc(self, armed):
        rows = _board()
        assert _short(_resolve(rows, "che-yes")) == (CFC, "yes", LONG, "premap_identity")
        h = _resolve(rows, "che-no")
        assert _short(h) == (CFC, "no", SHORT, "premap_identity")
        assert h["code_translated"] == {"che": "cfc"} and h["witness"] == CFC
        assert _short(_resolve(rows, "ars-yes")) == (ARS, "yes", LONG, "premap_identity")

    def test_b_the_shared_code_maps_by_identity_once_the_event_is_certified(self, armed):
        rows = _board()
        # the witness for `mun` is the other side of his event title
        h = _resolve(rows, "eve-yes")
        assert _short(h) == (EVE, "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        ex = _explain(rows, "eve-yes")
        assert ex["yn_c5"]["witness_src"] == "event" and ex["yn_c5"]["slug"] == f"epl-eve-mnu-{D}-eve"
        # no event title stored: his own -mun moneyline title, as trades stores it
        cert, tr = _certify(rows, FEED["eve-yes"][1], None, FEED["eve-yes"][0], HIS_ML)
        assert cert["slug"] == f"epl-eve-mnu-{D}-eve" and tr["witness_src"] == "trades"
        assert cert["code_translated"] == {"mun": "mnu"} and cert["witness"] == MNU

    def test_b_the_identity_lane_still_judges_the_rewritten_slug_by_its_own_rules(self, armed):
        """A title naming the OTHER club on the shared code's market: the
        event certifies, the identity arm refuses the row as it always
        did (yn:subj), by its own name."""
        rows = _board()
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), T_MUN, EVE_MNU, "Yes",
                                                FEED["eve-yes"][0]))
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:subj"
        assert ex["yn_c5"]["certified"] is True and "label" not in ex["yn_c5"]
        assert ex["yn_c5"]["lane"]["refusal"] == "yn:subj"
        assert asyncio.run(premap.resolve(_Pool(rows), T_MUN, EVE_MNU, "Yes",
                                          FEED["eve-yes"][0])) is None

    def test_a_moneyline_without_an_event_title_never_reaches_the_rows(self, armed):
        """A lookup fact, not a decision: his moneyline title builds no
        matchup key, so without markets.event_title the venue's rows are
        never fetched (no_key_intersection) -- C5 has nothing to read."""
        rows = _board()
        for key in ("mun-yes", "eve-yes", "che-yes", "spread"):
            assert _resolve(rows, key, ev=None, titles=HIS_ML) is None
            assert _explain(rows, key, ev=None, titles=HIS_ML)["step"] == "no_key_intersection"


# ------------------------------------------------------------ (c): the draw

class TestCTheDraw:
    def test_c_the_draw_maps_via_the_draw_witness(self, armed):
        rows = _board()
        h = _resolve(rows, "draw")
        assert _short(h) == (DRAW, "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        assert _explain(rows, "draw")["yn_c5"]["witness_src"] == "event"
        # no event title: his stored -mun title certifies the code, his own
        # draw title is the draw's witness (C3), as today
        h = _resolve(rows, "draw", ev=None, titles=HIS_ML)
        assert _short(h) == (DRAW, "yes", LONG, "premap_draw_title")
        assert _explain(rows, "draw", ev=None, titles=HIS_ML)["yn_c5"]["witness_src"] == "trades"
        assert _short(_resolve(rows, "ars-draw")) == (f"atc-epl-ars-cfc-{D}-draw", "yes", LONG,
                                                      "premap_identity")

    def test_the_draw_title_alone_certifies_no_code(self, armed):
        rows = _board()
        assert _resolve(rows, "draw", ev=None) is None
        ex = _explain(rows, "draw", ev=None)
        assert ex["step"] == "no_side_match" and ex["split"] == UNWIT
        assert ex["yn_c5"]["why"] == "no-event-title"


# ------------------------------------- (e): totals, btts, the spread

class TestETheFamiliesOfTheEvent:
    def test_e_the_full_game_total_maps_by_the_wording_arm_as_today(self, armed):
        """His title's matchup keys fetch the event and the line is unique
        on the full-game segment: the wording arm maps it before any
        identity arm runs -- no code is read, nothing to translate."""
        rows = _board()
        h = _resolve(rows, "total", ev=None)
        assert _short(h) == (f"tsc-epl-eve-mnu-{D}-2pt5", "under", SHORT, "premap")
        assert "code_translated" not in h
        assert _short(_resolve(rows, "total-over")) == (f"tsc-epl-eve-mnu-{D}-3pt5", "over", LONG,
                                                        "premap")

    def test_e_the_first_half_total_maps_by_c3_with_the_moneyline_witness(self, armed):
        rows = _board(extra=VENUE_HALVES)
        p = _Pool(rows, HIS_ML)
        h = _resolve(rows, "fh-total", ev=None, pool=p)
        assert _short(h) == (f"tsc-epl-eve-mnu-{D}-fh-1pt5", "under", SHORT, "premap_fh_total")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        assert p.reads == ["us_premap", "titles"]
        ex = _explain(rows, "fh-total", ev=None, titles=HIS_ML)
        assert ex["yn_c5"]["witness_src"] == "trades"
        assert ex["yn_c5"]["lane"]["matched_by"] == "premap_fh_total"
        assert ex["matched_by"] == "premap_fh_total" and ex["code_translated"] == {"mun": "mnu"}

    def test_btts_and_the_spread_ride_the_same_certification(self, armed):
        rows = _board()
        h = _resolve(rows, "btts")
        assert _short(h) == (f"astatc-epl-eve-mnu-{D}-btts", "yes", LONG, "premap_btts")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU
        assert _short(_resolve(rows, "ars-btts")) == (f"astatc-epl-ars-cfc-{D}-btts", "no", SHORT,
                                                      "premap_btts")
        # Manchester United -1.5 is b at -L: the venue's pos-1pt5 row on the
        # no side, BUY_SHORT (C3 §9.3's table, unchanged)
        h = _resolve(rows, "spread")
        assert _short(h) == (f"asc-epl-eve-mnu-{D}-pos-1pt5", "no", SHORT, "premap_spread")
        assert h["code_translated"] == {"mun": "mnu"} and h["witness"] == MNU

    def test_a_line_the_venue_does_not_list_refuses_by_the_lanes_own_name(self, armed):
        rows = _board(extra=VENUE_HALVES)
        slug = f"epl-eve-mun-{D}-first-half-total-3pt5"
        title = "Everton FC vs. Manchester United FC: 1st Half O/U 3.5"
        assert asyncio.run(premap.resolve(_Pool(rows, HIS_ML), title, None, "Under", slug)) is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows, HIS_ML), title, None, "Under", slug))
        assert ex["step"] == "no_side_match" and ex["split"] == "total:line-absent"
        assert ex["yn_c5"]["certified"] is True and "label" not in ex["yn_c5"]


# ---------------------------------------- (f): a name that is not his

class TestFANameThatIsNotHis:
    def test_f_a_venue_row_naming_another_club_refuses(self, armed):
        # the venue's game is Everton v Manchester City on both rows; his feed says Manchester United
        rows = _with_question(_board(), MNU, P4.format(a="Manchester City FC", b="Everton FC"))
        rows = _with_question(rows, EVE, P4.format(a="Everton FC", b="Manchester City FC"))
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["step"] == "no_side_match" and ex["split"] == MISMATCH
        assert ex["yn_c5"]["his_club"] == "manchester united fc"
        assert ex["yn_c5"]["venue_club"] == "manchester city fc"
        assert "certified" not in ex["yn_c5"]
        # the event is certified for NO family: the shared code, the draw and btts refuse with it
        for key in ("eve-yes", "draw", "btts"):
            assert _resolve(rows, key) is None, key
            assert _explain(rows, key)["split"] == MISMATCH, key

    def test_the_two_venue_rows_must_name_each_other(self, armed):
        rows = _with_question(_board(), MNU, P4.format(a="Manchester City FC", b="Everton FC"))
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["split"] == MISMATCH and ex["yn_c5"]["why"] == "rows-disagree"
        assert ex["yn_c5"]["venue_names"] == ["everton fc", "manchester city fc"]

    def test_his_title_naming_the_other_club_is_shear_never_rescued(self, armed):
        rows = _board()
        p = _Pool(rows, HIS_ML)
        ex = asyncio.run(premap.resolve_explain(p, "Will Everton FC win on 2026-09-06?", EVE_MNU,
                                                "Yes", S_MUN))
        assert ex["split"] == MISMATCH
        assert ex["yn_c5"]["his_club"] == "everton fc" and ex["yn_c5"]["witness_src"] == "title"
        assert p.reads == ["us_premap"]
        assert asyncio.run(premap.resolve(_Pool(rows, HIS_ML), "Will Everton FC win on 2026-09-06?",
                                          EVE_MNU, "Yes", S_MUN)) is None

    def test_an_event_title_naming_another_game_is_no_witness(self, armed):
        rows = _board()
        ex = _explain(rows, "btts", ev="Everton FC vs. Liverpool FC")
        assert ex["split"] == MISMATCH and ex["yn_c5"]["his_club"] == "liverpool fc"
        ex = _explain(rows, "btts", ev="Liverpool FC vs. Chelsea FC")
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "event-title-other-side"
        ex = _explain(rows, "btts", ev="Everton FC - Manchester United FC")
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "event-title-shape"

    def test_a_prefix_of_the_venues_name_is_not_his(self, armed):
        """Name EQUALITY (pmus._yn_name_match: token-set equality, no
        containment), pinned in both directions of a prefix (review v2,
        MEDIUM-1): 'Manchester' is a prefix of 'Manchester United FC' --
        a startswith reading either way would certify; equality refuses."""
        rows = _board()
        assert "manchester united fc".startswith("manchester")
        assert pmus._yn_name_match("manchester", "manchester united fc") is False
        # his title, on the -mun market
        title = "Will Manchester win on 2026-09-06?"
        assert asyncio.run(premap.resolve(_Pool(rows), title, EVE_MNU, "Yes", S_MUN)) is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), title, EVE_MNU, "Yes", S_MUN))
        assert ex["step"] == "no_side_match" and ex["split"] == MISMATCH
        assert ex["yn_c5"]["his_club"] == "manchester" and ex["yn_c5"]["witness_src"] == "title"
        assert ex["yn_c5"]["venue_club"] == "manchester united fc"
        # his event title, on btts
        assert _resolve(rows, "btts", ev="Everton FC vs. Manchester") is None
        ex = _explain(rows, "btts", ev="Everton FC vs. Manchester")
        assert ex["split"] == MISMATCH and ex["yn_c5"]["his_club"] == "manchester"
        assert ex["yn_c5"]["witness_src"] == "event"
        # his stored moneyline title, on btts
        short = {f"epl-eve-mun-{D}-mun": ["Will Manchester win on 2026-09-06?"]}
        ex = _explain(rows, "btts", ev=None, titles=short)
        assert ex["split"] == MISMATCH and ex["yn_c5"]["his_club"] == "manchester"
        # and the longer side: the venue naming more than his words do
        longer = _with_question(rows, MNU, P4.format(a="Manchester United FC Salford", b="Everton FC"))
        longer = _with_question(longer, EVE, P4.format(a="Everton FC", b="Manchester United FC Salford"))
        assert _resolve(longer, "mun-yes") is None
        assert _explain(longer, "mun-yes")["split"] == MISMATCH

    def test_stored_titles_that_disagree_witness_nothing(self, armed):
        rows = _board()
        two = {f"epl-eve-mun-{D}-mun": [T_MUN, "Will Manchester City FC win on 2026-09-06?"]}
        ex = _explain(rows, "btts", ev=None, titles=two)
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "titles-disagree"


class TestSwappedQuestions:
    """Observation B (review v2): the venue's two per-team questions
    swapped against their identifiers -- the -mnu row asks about Everton,
    the -eve row about Manchester United; the rows still name each other."""
    def _rows(self):
        rows = _with_question(_board(), MNU, P4.format(a="Everton FC", b="Manchester United FC"))
        return _with_question(rows, EVE, P4.format(a="Manchester United FC", b="Everton FC"))

    def test_where_his_words_bind_the_code_the_swap_is_named(self, armed):
        rows = self._rows()
        # his own title on the -mun market names the SHARED row's subject
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["split"] == MISMATCH and ex["yn_c5"]["why"] == "rows-swapped"
        assert ex["yn_c5"]["his_club"] == "manchester united fc"
        assert ex["yn_c5"]["venue_club"] == "everton fc"
        # his stored -mun title, on btts and the draw
        for key in ("btts", "draw"):
            assert _resolve(rows, key, ev=None, titles=HIS_ML) is None, key
            ex = _explain(rows, key, ev=None, titles=HIS_ML)
            assert ex["split"] == MISMATCH and ex["yn_c5"]["why"] == "rows-swapped", key
            assert ex["yn_c5"]["witness_src"] == "trades"

    def test_where_only_his_event_title_witnesses_the_record_shows_both_subjects(self, armed):
        """Nothing of his binds a code to a name in an event title, so the
        pair certifies on the names the rows carry; the record carries the
        club the witness matched (his_club = venue_club, the unshared
        row's subject) beside the shared row's subject, so the swap is
        readable -- and the family lanes read names on their own rows, so
        no side is wrong: the moneylines refuse, btts / spread / draw land
        on the event's own rows."""
        rows = self._rows()
        for key in ("btts", "spread", "draw"):
            h = _resolve(rows, key)
            assert h is not None and h["code_translated"] == {"mun": "mnu"}, key
            c5 = _explain(rows, key)["yn_c5"]
            assert c5["his_club"] == c5["venue_club"] == "everton fc"
            assert c5["shared_club"] == "manchester united fc" and c5["witness_src"] == "event"
        assert _short(_resolve(rows, "btts"))[0] == f"astatc-epl-eve-mnu-{D}-btts"
        assert _short(_resolve(rows, "spread")) == (f"asc-epl-eve-mnu-{D}-pos-1pt5", "no", SHORT,
                                                    "premap_spread")
        assert _resolve(rows, "eve-yes") is None and _explain(rows, "eve-yes")["split"] == "yn:subj"
        assert _resolve(rows, "mun-yes") is None

    def test_an_unswapped_pair_records_the_same_fields(self, armed):
        c5 = _explain(_board(), "btts")["yn_c5"]
        assert c5["his_club"] == c5["venue_club"] == "manchester united fc"
        assert c5["shared_club"] == "everton fc"


# ------------------------------------------------- (g): ambiguity refuses

class TestGAmbiguity:
    def test_g_two_candidate_events_sharing_the_code_refuse(self, armed):
        # Everton listed twice on the date under epl, in the home slot both times
        twin = {f"epl-eve-mci-{D}": ("Everton FC vs. Manchester City FC", _game(
            "epl", "eve", "mci", "Everton FC", "Manchester City FC", "EVE vs MCI", "9:00AM"))}
        rows = _board(extra=twin)
        cert, tr = _certify(rows, T_MUN, EVE_MNU, S_MUN)
        assert cert is None and tr["refusal"] == AMBIG
        assert tr["candidates"] == [f"epl-eve-mci-{D}", f"epl-eve-mnu-{D}"]
        # end to end: a same-day twin listing under another club code, titled
        # the same, is fetched by his keys and refuses -- and the census says so
        dup = {f"epl-eve-manu-{D}": (EVE_MNU, _game(
            "epl", "eve", "manu", "Everton FC", "Manchester United FC", "EVE vs MANU", "9:00AM"))}
        rows = _board(extra=dup)
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["step"] == "no_side_match" and ex["split"] == AMBIG
        ctx = {"title": T_MUN, "event_title": EVE_MNU, "outcome": "Yes", "his_slug": S_MUN}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == f"no_side_match:{AMBIG}"
        for key in ("eve-yes", "draw", "btts", "spread"):
            assert _resolve(rows, key) is None, key


# ------------------------------------------------- (h): unwitnessed refuses

class TestHUnwitnessed:
    @pytest.mark.parametrize("key", ["fh-total", "btts", "draw", "ars-btts"])
    def test_h_no_club_in_his_title_no_moneyline_of_his_no_event_title(self, armed, key):
        rows = _board(extra=VENUE_HALVES)
        p = _Pool(rows)
        assert _resolve(rows, key, ev=None, pool=p) is None
        assert p.reads == ["us_premap", "titles"]      # the read was made and found nothing
        ex = _explain(rows, key, ev=None)
        assert ex["step"] == "no_side_match" and ex["split"] == UNWIT
        assert ex["yn_c5"]["why"] == "no-event-title"
        slug, title, _ev, oc = FEED[key]
        ctx = {"title": title, "event_title": None, "outcome": oc, "his_slug": slug}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == f"no_side_match:{UNWIT}"

    def test_the_stored_title_read_is_one_bounded_query_through_existing_indexes(self):
        """Both legs read through an index that exists (review v2, LOW-2):
        trades by trades_ts_idx (a 3-day bound), markets by
        markets_event_idx on his event slug; no new index (migrate.py runs
        each file in a transaction, so no CONCURRENTLY, and a plain index
        on trades would lock the ingestion writers on the boot path)."""
        sql = premap._C5_TITLES_SQL
        assert "LIMIT 9" in sql and sql.count("$1") == 2 and sql.count("$2") == 1
        assert "ts >= now() - interval '3 days'" in sql and "event_slug = $2 AND slug = $1" in sql
        assert "FROM trades" in sql and "FROM markets" in sql and "event_title" not in sql
        assert premap._C5_TITLES_MAX == 8

    def test_nine_stored_titles_overflow_the_bound(self, armed):
        """LIMIT 9 with no ORDER BY: nine rows back means the store may
        hold a tenth that disagrees, so nine is refused outright -- never
        eight of them read as agreement."""
        rows = _board()
        eight = {f"epl-eve-mun-{D}-mun": [T_MUN + " " * i for i in range(8)]}
        ex = _explain(rows, "btts", ev=None, titles=eight)
        assert ex["step"] == "resolves" and ex["yn_c5"]["witness_src"] == "trades"
        nine = {f"epl-eve-mun-{D}-mun": [T_MUN + " " * i for i in range(9)]}
        assert _resolve(rows, "btts", ev=None, titles=nine) is None
        ex = _explain(rows, "btts", ev=None, titles=nine)
        assert ex["split"] == "yn:code-translate:titles-overflow"
        assert ex["yn_c5"]["refusal"] == "yn:code-translate:titles-overflow"
        assert ex["yn_c5"]["why"] == "titles-overflow"
        mixed = {f"epl-eve-mun-{D}-mun": [T_MUN] * 8 + ["Will Manchester City FC win on 2026-09-06?"]}
        assert _explain(rows, "btts", ev=None, titles=mixed)["split"] == \
            "yn:code-translate:titles-overflow"

    def test_an_unreadable_store_is_unwitnessed_never_a_guess(self, armed):
        class _Broken(_Pool):
            async def fetch(self, sql, *a):
                if "us_premap" in sql:
                    return await super().fetch(sql, *a)
                raise RuntimeError("db down")
        rows = _board()
        assert asyncio.run(premap.resolve(_Broken(rows), FEED["btts"][1], None, "Yes",
                                          FEED["btts"][0])) is None
        ex = asyncio.run(premap.resolve_explain(_Broken(rows), FEED["btts"][1], None, "Yes",
                                                FEED["btts"][0]))
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "no-event-title"


# ------------------------------------ (j): scoped to the event, never reused

class TestJTheTranslationIsScopedToTheEvent:
    def test_j_never_reused_across_dates(self, armed):
        rows = _board()
        assert _short(_resolve(rows, "mun-yes"))[0] == MNU
        # the same rows, his slug a day later: no candidate event, nothing certified, no trace
        cert, tr = _certify(rows, "Will Manchester United FC win on 2026-09-07?", EVE_MNU,
                            "epl-eve-mun-2026-09-07-mun")
        assert cert is None and tr == {}
        # the return fixture a week later derives its own witness from its own rows
        later = {"epl-eve-mnu-2026-09-13": (EVE_MNU, [
            _yn("atc-epl-eve-mnu-2026-09-13-mnu", "Will Manchester United FC win against Everton FC "
                "in the Premier League match scheduled for Sep 13, 2026?"),
            _yn("atc-epl-eve-mnu-2026-09-13-eve", "Will Everton FC win against Manchester United FC "
                "in the Premier League match scheduled for Sep 13, 2026?")])}
        rows2 = _board(extra=later)
        h = asyncio.run(premap.resolve(_Pool(rows2), "Will Manchester United FC win on 2026-09-13?",
                                       EVE_MNU, "Yes", "epl-eve-mun-2026-09-13-mun"))
        assert h["market_slug"] == "atc-epl-eve-mnu-2026-09-13-mnu"
        assert h["witness"] == "atc-epl-eve-mnu-2026-09-13-mnu"
        h = _resolve(rows2, "mun-yes")
        assert h["market_slug"] == MNU and h["witness"] == MNU
        # a date the venue lists nothing for: no rows, no translation
        ex = asyncio.run(premap.resolve_explain(_Pool(rows2), "Will Manchester United FC win on "
                                                "2026-09-07?", EVE_MNU, "Yes",
                                                "epl-eve-mun-2026-09-07-mun"))
        assert ex["step"] == "no_key_intersection" and "yn_c5" not in ex

    def test_j_never_reused_for_another_opponent(self, armed):
        rows = _board()
        cert, tr = _certify(rows, T_MUN, "Liverpool FC vs. Manchester United FC",
                            f"epl-liv-mun-{D}-mun")
        assert cert is None and tr == {}

    def test_nothing_is_remembered_between_calls(self, armed):
        rows = _board()
        h1 = _resolve(rows, "mun-yes")
        h2 = _resolve(rows, "mun-yes")
        assert h1["code_translated"] == h2["code_translated"]
        assert h1["code_translated"] is not h2["code_translated"]
        assert not any(n.lower().startswith("_c5") and isinstance(getattr(premap, n), dict)
                       for n in dir(premap))


# ------------------------------- the trigger is identity, not similarity

class TestTheTriggerIsIdentityNotSimilarity:
    def test_his_own_event_on_the_board_belongs_to_the_identity_arm(self, armed):
        own = {f"epl-eve-mun-{D}": (EVE_MNU, [
            _yn(f"atc-epl-eve-mun-{D}-mun", P4.format(a="Manchester United FC", b="Everton FC")),
            _yn(f"atc-epl-eve-mun-{D}-eve", P4.format(a="Everton FC", b="Manchester United FC"))])}
        rows = _board(extra=own)
        h = _resolve(rows, "mun-yes")
        assert _short(h) == (f"atc-epl-eve-mun-{D}-mun", "yes", LONG, "premap_identity")
        assert "code_translated" not in h
        # his own identifier refused (the venue's `mun` names another club) is never translated around
        bad = _with_question(rows, f"atc-epl-eve-mun-{D}-mun",
                             P4.format(a="Manchester City FC", b="Everton FC"))
        assert _resolve(bad, "mun-yes") is None
        ex = _explain(bad, "mun-yes")
        assert ex["split"] == "yn:subj" and "yn_c5" not in ex

    def test_the_position_must_agree_home_stays_home(self, armed):
        rev = {f"epl-mnu-eve-{D}": ("Manchester United FC vs. Everton FC", [
            _yn(f"atc-epl-mnu-eve-{D}-mnu", P4.format(a="Manchester United FC", b="Everton FC")),
            _yn(f"atc-epl-mnu-eve-{D}-eve", P4.format(a="Everton FC", b="Manchester United FC"))])}
        rows = _board(venue={}, extra=rev)
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:no-row" and "yn_c5" not in ex

    def test_a_code_elsewhere_on_the_board_is_never_a_candidate(self, armed):
        # the census's `mun` in another league's home slot and Arsenal's wsl
        # twin in the away slot (epl_rows_0004.log:187, :232): neither shares
        # a code in his position under his league
        noise = {
            f"lng-mun-dsp-{D}": ("Municipal vs. Diriangen", [
                _yn(f"atc-lng-mun-dsp-{D}-mun", "Will Municipal win against Diriangen in the "
                    "Liga Primera match scheduled for Sep 6, 2026?"),
                _yn(f"atc-lng-mun-dsp-{D}-dsp", "Will Diriangen win against Municipal in the "
                    "Liga Primera match scheduled for Sep 6, 2026?")]),
            f"wsl-brh-ars-{D}": ("Brighton Women vs. Arsenal Women", [
                _yn(f"atc-wsl-brh-ars-{D}-brh", "Will Brighton win against Arsenal in the WSL "
                    "match scheduled for Sep 6, 2026?"),
                _yn(f"atc-wsl-brh-ars-{D}-ars", "Will Arsenal win against Brighton in the WSL "
                    "match scheduled for Sep 6, 2026?")]),
        }
        rows = _board(venue={}, extra=noise)
        for key in ("mun-yes", "che-yes", "ars-yes"):
            slug, title, ev, _oc = FEED[key]
            cert, tr = _certify(rows, title, ev, slug)
            assert cert is None and tr == {}, key

    def test_similarity_of_codes_is_not_evidence(self, armed):
        # a code with nothing in common with his is certified by the question alone ...
        xyz = {f"epl-eve-xyz-{D}": (EVE_MNU, _game(
            "epl", "eve", "xyz", "Everton FC", "Manchester United FC", "EVE vs XYZ", "9:00AM"))}
        rows = _board(venue={}, extra=xyz)
        h = _resolve(rows, "mun-yes")
        assert _short(h) == (f"atc-epl-eve-xyz-{D}-xyz", "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"mun": "xyz"}
        # ... while the near-twin `mnu` naming another club refuses (test_f);
        # and no table of codes exists anywhere in the module
        assert not any(isinstance(getattr(premap, n), dict) and "mun" in getattr(premap, n)
                       for n in dir(premap))

    @pytest.mark.parametrize("dropped", [{MNU}, {EVE}, {MNU, EVE}])
    def test_a_half_row_or_a_lone_row_is_no_witness(self, armed, dropped):
        """The unshared row alone, the shared row alone (review v2,
        LOW-3), or neither: no witness, today's reason, no C5 trace."""
        rows = _board(extra=VENUE_HALVES, drop=dropped)
        for key in ("mun-yes", "eve-yes", "draw", "btts"):
            assert _resolve(rows, key, titles=HIS_ML) is None, key
            ex = _explain(rows, key, titles=HIS_ML)
            assert ex["step"] == "no_side_match" and "yn_c5" not in ex, key
            assert ex["split"] in ("yn:no-row", "btts:no-row"), key


# ------------------------------------ the venue rows must be the contract

class TestTheVenueRowsMustBeTheContract:
    def _ex(self, rows):
        assert _resolve(rows, "mun-yes") is None
        return _explain(rows, "mun-yes")

    def test_a_lined_row(self, armed):
        rows = [dict(r, line="6") if r["identifier"] == MNU else dict(r) for r in _board()]
        ex = self._ex(rows)
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "venue-lined"

    def test_a_swapped_intent(self, armed):
        rows = [dict(r, intent=(SHORT if r["intent"] == LONG else LONG))
                if r["identifier"] == MNU else dict(r) for r in _board()]
        ex = self._ex(rows)
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "venue-intent"

    def test_one_side_only(self, armed):
        rows = [r for r in _board() if not (r["identifier"] == MNU and r["side_norm"] == "no")]
        ex = self._ex(rows)
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "venue-sides"

    def test_another_shape_another_date_a_scoped_league(self, armed):
        ex = self._ex(_with_question(_board(), MNU, "Will Manchester United FC win?"))
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "unshared:yn:shape"
        ex = self._ex(_with_question(_board(), MNU, P4.format(
            a="Manchester United FC", b="Everton FC").replace("Sep 6", "Sep 7")))
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "unshared:yn:qdate"
        ex = self._ex(_with_question(_board(), EVE, P4.format(
            a="Everton FC", b="Manchester United FC").replace("Premier League", "Premier League U21")))
        assert ex["split"] == UNWIT and ex["yn_c5"]["why"] == "shared:yn:league-slot"

    def test_the_per_team_question_reading_is_the_one_c2_uses(self):
        gd, why = premap._yn_team_question(P4.format(a="Manchester United FC", b="Everton FC"), D)
        assert why is None and gd == {"subj": "manchester united fc", "opp": "everton fc",
                                      "lg": "premier league"}
        row = {"question": P4.format(a="Manchester United FC", b="Everton FC")}
        assert premap._yn_team_row(row, d=D, anchor="manchester united fc", opp_witness=None) is None
        assert premap._yn_team_row(row, d=D, anchor="everton fc", opp_witness=None) == "yn:subj"
        assert premap._yn_team_row(row, d="2026-09-07", anchor="manchester united fc",
                                   opp_witness=None) == "yn:qdate"
        assert pmus._yn_name_match("manchester united fc", "manchester united")


# -------------------------------------------------- under a league alias

class TestUnderALeagueAlias:
    """An EXPECTATION about the rule, not an attested game: tonight's
    Ligue 1 rows carry his own codes (atc-lg1-tro-str-…-tro,
    epl_rows_0004.log:174). Built the way C2's VENUE_EXTRA was: the
    venue's own wording under its own league code."""
    ROWS = {f"lg1-tro-rcs-{D}": ("ES Troyes AC vs. RC Strasbourg Alsace", [
        _yn(f"atc-lg1-tro-rcs-{D}-tro", "Will ES Troyes AC win against RC Strasbourg Alsace in "
            "the Ligue 1 match scheduled for Sep 6, 2026?"),
        _yn(f"atc-lg1-tro-rcs-{D}-rcs", "Will RC Strasbourg Alsace win against ES Troyes AC in "
            "the Ligue 1 match scheduled for Sep 6, 2026?")])}
    S, T = f"fl1-tro-str-{D}-str", "Will RC Strasbourg Alsace win on 2026-09-06?"
    EV = "ES Troyes AC vs. RC Strasbourg Alsace"

    def test_both_clubs_in_his_words_certify_the_pair_under_the_alias(self, armed):
        rows = _board(venue={}, extra=self.ROWS)
        h = asyncio.run(premap.resolve(_Pool(rows), self.T, self.EV, "Yes", self.S))
        assert _short(h) == (f"atc-lg1-tro-rcs-{D}-rcs", "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"str": "rcs"} and h["league_alias"] == "fl1->lg1"
        ex = asyncio.run(premap.resolve_explain(_Pool(rows), self.T, self.EV, "Yes", self.S))
        assert ex["league_alias"] == "fl1->lg1" and ex["yn_c5"]["witness_src"] == "title"

    def test_without_his_words_on_the_shared_club_it_is_unwitnessed(self, armed):
        rows = _board(venue={}, extra=self.ROWS)
        cert, tr = _certify(rows, self.T, None, self.S)
        assert cert is None and tr["refusal"] == UNWIT and tr["why"] == "alias:no-event-title"
        cert, tr = _certify(rows, self.T, "Paris FC vs. RC Strasbourg Alsace", self.S)
        assert cert is None and tr["refusal"] == MISMATCH and tr["his_club"] == "paris fc"

    def test_two_league_codes_carrying_a_candidate_refuse(self, armed):
        twin = {f"lg2-tro-rcs-{D}": (self.EV, [
            _yn(f"atc-lg2-tro-rcs-{D}-tro", "Will ES Troyes AC win against RC Strasbourg Alsace "
                "in the Ligue 2 match scheduled for Sep 6, 2026?"),
            _yn(f"atc-lg2-tro-rcs-{D}-rcs", "Will RC Strasbourg Alsace win against ES Troyes AC "
                "in the Ligue 2 match scheduled for Sep 6, 2026?")])}
        rows = _board(venue={}, extra={**self.ROWS, **twin})
        cert, tr = _certify(rows, self.T, self.EV, self.S)
        assert cert is None and tr["refusal"] == AMBIG and tr["league_codes"] == ["lg1", "lg2"]


# ----------------------------------------------- the switch, the census

class TestTheSwitchAndTheCensus:
    def test_dark_every_code_bound_row_answers_as_before(self, dark):
        rows = _board(extra=VENUE_HALVES)
        for key in FEED:
            if key in ("total", "total-over"):
                continue                       # the wording arm's, switch or no switch
            assert _resolve(rows, key, titles=HIS_ML) is None, key
            assert "yn_c5" not in _explain(rows, key, titles=HIS_ML), key

    def test_the_reason_names_are_closed(self):
        src = inspect.getsource(premap)
        for n in ("yn:code-translated", AMBIG, UNWIT, MISMATCH, "yn:picked-vetoed",
                  "yn:code-translate:titles-overflow"):
            assert f'"{n}"' in src, n

    def test_picked_then_vetoed_is_named(self, armed):
        """His own identifier on the board with a stamped line: _yn_pick
        picks it and match_side vetoes it -- the one current path that
        left the label bare (the 2026-09-06 epl investigation §1.3)."""
        own = {f"epl-eve-mun-{D}": (EVE_MNU, [
            _yn(f"atc-epl-eve-mun-{D}-mun", P4.format(a="Manchester United FC", b="Everton FC"))])}
        rows = [dict(r, line="6") if r["identifier"] == f"atc-epl-eve-mun-{D}-mun" else dict(r)
                for r in _board(venue={}, extra=own)]
        assert _resolve(rows, "mun-yes") is None
        ex = _explain(rows, "mun-yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:picked-vetoed"
        assert ex["yn_c2"]["matched_by"] == "premap_identity" and "yn_c5" not in ex

    def test_the_census_row_is_stamped_with_when_it_was_written(self):
        from pathlib import Path

        from sportsassets.analytics import mirror_report
        yml = Path(premap.__file__).resolve().parents[3] / ".github" / "workflows" / \
            "engine-diagnostic.yml"
        line = next(ln for ln in yml.read_text().splitlines() if "MIRRORUNMAPMKT \\(" in ln)
        assert 'at=\\(.at // "-")' in line
        assert '"at", "whale", "condition_id"' in inspect.getsource(mirror_report.summarize)

    def test_map_market_keeps_source_premap(self, armed):
        from tests.test_c1_round2 import _premap_pool
        from tests.test_mirror_maps_the_copy_lane import _fresh, _yesno_fills  # noqa: F401
        from tests.test_mirror_shadow import CID
        rows = _board()
        slug, title, ev, _oc = FEED["mun-yes"]
        fills = _yesno_fills(slug, title, ev, f"epl-eve-mun-{D}")
        m = asyncio.run(ms.map_market(_premap_pool(fills, rows), fills, None, whale="rn1",
                                      condition_id=CID))
        assert m and m["source"] == "premap" and m["us_slug"] == MNU
        ms._map_cache.clear()
