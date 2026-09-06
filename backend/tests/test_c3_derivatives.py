"""C3 (2026-09-06): the derivative families by identity -- spreads,
first-half totals, btts, the draw witnessed by his own title, the shared
team-code stem -- and the families the venue does not list (exact score,
halftime result, team totals), refused by name.

The venue rows are the c3_rows_2038.log / c3_rows_2041.log dumps
(identifier + question verbatim; the asc side expansion is the venue's
own shape: two sides sharing the slug, described by the line, marked
long/short). His rows are c3_rows_2039.log table 2, verbatim.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import map_lane
from sportsassets.copy_sports import family_of, segment_of
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"


def _asc(ident: str, q: str) -> dict:
    line = ident.rsplit("-", 1)[-1].replace("pt", ".")
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": line, "long": True},
        {"identifier": ident, "description": line, "long": False}]}


def _tsc(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Over", "long": True},
        {"identifier": ident, "description": "Under", "long": False}]}


def _yn(ident: str, q: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


EVENTS = {
    "aec-cfb-scarst-flam-2026-09-06": "South Carolina State vs. Florida A&M",
    "aec-cfb-washst-wash-2026-09-06": "Washington State vs. Washington",
    "aec-lal-esp-sev-2026-09-06": "RCD Espanyol de Barcelona vs. Sevilla FC",
    "aec-lg1-olm-pfc-2026-09-06": "Olympique de Marseille vs. Paris FC",
    "aec-sea-juv-mil-2026-09-06": "Juventus FC vs. AC Milan",
}
SOCCER = {
    "lal-esp-sev": ("RCD Espanyol de Barcelona", "Sevilla FC", "ESP vs SEV", "La Liga", "3:00PM"),
    "lg1-olm-pfc": ("Olympique de Marseille", "Paris FC", "OLM vs PFC", "Ligue 1", "2:45PM"),
    "sea-juv-mil": ("Juventus FC", "AC Milan", "JUV vs MIL", "Serie A", "2:45PM"),
}


def _venue() -> list[tuple[str, dict]]:
    """(event slug, market) for every row of the two dumps this build
    reads -- the cfb asc rows of 2038 (nicknames on most lines, the
    schools on 22.5/24.5), the soccer asc/tsc/astatc/atc rows of 2041."""
    out: list[tuple[str, dict]] = []
    ev = "aec-cfb-scarst-flam-2026-09-06"
    for ln in ("10pt5", "14pt5", "1pt5", "21pt5", "3pt5", "5pt5", "7pt5"):
        L = ln.replace("pt", ".")
        out.append((ev, _asc(f"asc-cfb-scarst-flam-{D}-neg-{ln}",
                             f"Will the Bulldogs cover -{L} vs the Rattlers in Bulldogs vs. Rattlers?")))
        out.append((ev, _asc(f"asc-cfb-scarst-flam-{D}-pos-{ln}",
                             f"Will the Bulldogs cover {L} vs the Rattlers in Bulldogs vs. Rattlers?")))
    for ln in ("22pt5", "24pt5"):
        L = ln.replace("pt", ".")
        out.append((ev, _asc(f"asc-cfb-scarst-flam-{D}-neg-{ln}",
                             f"Will the South Carolina State cover -{L} vs the Florida A&M in "
                             f"South Carolina State vs. Florida A&M?")))
    ev = "aec-cfb-washst-wash-2026-09-06"
    for ln in ("21pt5", "23pt5", "24pt5"):
        L = ln.replace("pt", ".")
        out.append((ev, _asc(f"asc-cfb-washst-wash-{D}-pos-{ln}",
                             f"Will the Cougars cover {L} vs the Huskies in Cougars vs. Huskies?")))
        out.append((ev, _asc(f"asc-cfb-washst-wash-{D}-neg-{ln}",
                             f"Will the Cougars cover -{L} vs the Huskies in Cougars vs. Huskies?")))
    for seg in ("", "-1h"):
        out.append((ev, _asc(f"asc-cfb-washst-wash-{D}{seg}-pos-3pt5",
                             "Will the Washington State cover 3.5 vs the Washington in "
                             "Washington State vs. Washington?")))
    for key, (A, B, codes, lg, clock) in SOCCER.items():
        ev = f"aec-{key}-{D}"
        for seg in ("", "-fh", "-sh"):
            for ln in ("1pt5", "2pt5"):
                L = ln.replace("pt", ".")
                out.append((ev, _asc(f"asc-{key}-{D}{seg}-neg-{ln}",
                                     f"Will the {A} cover -{L} vs the {B} in {codes}?")))
                out.append((ev, _asc(f"asc-{key}-{D}{seg}-pos-{ln}",
                                     f"Will the {A} cover {L} vs the {B} in {codes}?")))
            lines = ("0pt5", "1pt5", "2pt5", "3pt5") if not seg else \
                ("0pt5", "1pt5", "2pt5", "3pt5", "4pt5")
            for ln in lines:
                L = ln.replace("pt", ".")
                out.append((ev, _tsc(f"tsc-{key}-{D}{seg}-{ln}",
                                     f"Will the total in {codes} be more than {L}?")))
            word = {"": "match", "-fh": "first half", "-sh": "second half"}[seg]
            out.append((ev, _yn(f"astatc-{key}-{D}{seg}-btts",
                                f"Will both teams score in the {word} between {A} and {B} on "
                                f"{D} {clock} ET?")))
        out.append((ev, _yn(f"atc-{key}-{D}-draw",
                            f"Will the {lg} match {A} vs {B} scheduled for Sep 6, 2026 end in a draw?")))
        a_code = key.split("-")[1]
        out.append((ev, _yn(f"atc-{key}-{D}-{a_code}",
                            f"Will {A} win against {B} in the {lg} match scheduled for Sep 6, 2026?")))
    return out


def _board(venue=None, extra=(), drop=()) -> list[dict]:
    rows: list[dict] = []
    for ev, m in list(venue if venue is not None else _venue()) + list(extra):
        if m["slug"] in drop:
            continue
        keys = premap.event_keys_for(EVENTS[ev], ev)
        for r in premap._market_rows({"slug": ev, "title": EVENTS[ev]}, m):
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


def _resolve(rows, slug, title, outcome, ev=None):
    return asyncio.run(premap.resolve(_Pool(rows), title, ev, outcome, slug))


def _explain(rows, slug, title, outcome, ev=None):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, outcome, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"], h.get("league_alias"))


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# ------------------------------------------------ 1. the asc side parser

class TestTheAscSideIsItsMarker:
    def test_the_venues_spread_market_writes_a_yes_and_a_no_row(self):
        rows = premap._market_rows({"slug": "e", "title": "t"},
                                   _asc(f"asc-cfb-scarst-flam-{D}-neg-10pt5",
                                        "Will the Bulldogs cover -10.5 vs the Rattlers in Bulldogs vs. Rattlers?"))
        assert [(r["side_norm"], r["intent"], r["line"]) for r in rows] == [
            ("yes", LONG, "10.5"), ("no", SHORT, "10.5")]
        assert {r["identifier"] for r in rows} == {f"asc-cfb-scarst-flam-{D}-neg-10pt5"}
        # the two rows are DISTINCT on the (identifier, side_norm) index
        assert len({(r["identifier"], r["side_norm"]) for r in rows}) == 2

    def test_named_sides_keep_the_old_rows(self):
        # the legacy asc shape (team + line on each side) is untouched
        m = {"slug": "asc-nfl-kc-buf-2026-09-13", "question": "Spread",
             "marketSides": [{"identifier": "asc-kc", "description": "Chiefs -3", "long": True},
                             {"identifier": "asc-buf", "description": "Bills +3", "long": False}]}
        rows = premap._market_rows({"slug": "e", "title": "t"}, m)
        assert [r["side_norm"] for r in rows] == [premap._norm("Chiefs -3"), premap._norm("Bills +3")]
        assert [r["signed"] for r in rows] == ["-3", "+3"]

    @pytest.mark.parametrize("markers", [(True, True), (None, False), (None, None)])
    def test_without_one_long_and_one_short_marker_nothing_is_deduced(self, markers):
        ident = f"asc-cfb-scarst-flam-{D}-neg-10pt5"
        m = _asc(ident, "Will the Bulldogs cover -10.5 vs the Rattlers in Bulldogs vs. Rattlers?")
        for s, mk in zip(m["marketSides"], markers):
            if mk is None:
                del s["long"]
            else:
                s["long"] = mk
        rows = premap._market_rows({"slug": "e", "title": "t"}, m)
        assert all(r["side_norm"] not in ("yes", "no") for r in rows)

    def test_a_side_with_its_own_identifier_is_not_this_shape(self):
        ident = f"asc-cfb-scarst-flam-{D}-neg-10pt5"
        m = _asc(ident, "Will the Bulldogs cover -10.5 vs the Rattlers in Bulldogs vs. Rattlers?")
        m["marketSides"][1]["identifier"] = ident + "-no"
        rows = premap._market_rows({"slug": "e", "title": "t"}, m)
        assert all(r["side_norm"] not in ("yes", "no") for r in rows)

    def test_the_stale_digit_rows_are_inert(self, armed):
        # a row the old parser wrote ('10 50', BUY_SHORT) beside the new
        # yes/no rows is never a candidate: the pick reads yes/no only
        rows = _board()
        stale = dict(next(r for r in rows if r["identifier"] == f"asc-lal-esp-sev-{D}-neg-1pt5"),
                     side_norm="1 50", intent=SHORT)
        h = _resolve(rows + [stale], f"lal-esp-sev-{D}-spread-home-1pt5",
                     "Spread: RCD Espanyol de Barcelona (-1.5)", "RCD Espanyol de Barcelona")
        assert _short(h) == (f"asc-lal-esp-sev-{D}-neg-1pt5", "yes", LONG, "premap_spread", None)


# ----------------------------------------- 2. the spread sign/complement

ESP, SEV = "RCD Espanyol de Barcelona", "Sevilla FC"
S_ESP = f"lal-esp-sev-{D}-spread-home-1pt5"


class TestTheSpreadTable:
    """a = the first slug team = the venue question's subject (ESP);
    b = SEV. His team is his OUTCOME; his signed line is the title's
    when the title names his team, else its negation."""

    @pytest.mark.parametrize("outcome,title,want_id,want_side,want_intent", [
        # his team is a
        (ESP, f"Spread: {ESP} (-1.5)", "neg", "yes", LONG),     # a, -L -> neg yes
        (ESP, f"Spread: {ESP} (+1.5)", "pos", "yes", LONG),     # a, +L -> pos yes
        (ESP, f"Spread: {SEV} (-1.5)", "pos", "yes", LONG),     # title names b at -L: a has +L
        (ESP, f"Spread: {SEV} (+1.5)", "neg", "yes", LONG),     # title names b at +L: a has -L
        # his team is b
        (SEV, f"Spread: {SEV} (-1.5)", "pos", "no", SHORT),     # b, -L -> a covers +L is the complement
        (SEV, f"Spread: {SEV} (+1.5)", "neg", "no", SHORT),     # b, +L -> neg no
        (SEV, f"Spread: {ESP} (-1.5)", "neg", "no", SHORT),     # title names a at -L: b has +L
        (SEV, f"Spread: {ESP} (+1.5)", "pos", "no", SHORT),     # title names a at +L: b has -L
    ])
    def test_eight_cases(self, armed, outcome, title, want_id, want_side, want_intent):
        h = _resolve(_board(), S_ESP, title, outcome)
        assert _short(h) == (f"asc-lal-esp-sev-{D}-{want_id}-1pt5", want_side, want_intent,
                             "premap_spread", None), (outcome, title)

    def test_the_feeds_own_rows(self, armed):
        rows = _board()
        # title names the OTHER team: his team gets the points (his event
        # title is what carries the keys past the fl1/lg1 league code, as
        # the shadow's own no_side_match on this row says it did)
        assert _short(_resolve(rows, f"fl1-olm-pfc-{D}-spread-home-1pt5",
                               "Spread: Olympique de Marseille (-1.5)", "Paris FC",
                               ev="Olympique de Marseille vs. Paris FC")) == \
            (f"asc-lg1-olm-pfc-{D}-neg-1pt5", "no", SHORT, "premap_spread", "fl1->lg1")
        assert _explain(rows, f"fl1-olm-pfc-{D}-spread-home-1pt5",
                        "Spread: Olympique de Marseille (-1.5)", "Paris FC")["step"] == "no_key_intersection"
        assert _short(_resolve(rows, S_ESP, f"Spread: {ESP} (-1.5)", SEV)) == \
            (f"asc-lal-esp-sev-{D}-neg-1pt5", "no", SHORT, "premap_spread", None)
        assert _short(_resolve(rows, f"sea-juv-mil-{D}-spread-away-1pt5",
                               "Spread: AC Milan (-1.5)", "AC Milan")) == \
            (f"asc-sea-juv-mil-{D}-pos-1pt5", "no", SHORT, "premap_spread", None)

    def test_home_away_in_his_slug_is_never_a_position(self, armed):
        # the same title/outcome under 'away' maps identically: the team
        # is his outcome by NAME, the slug's home/away word decides nothing
        a = _resolve(_board(), S_ESP, f"Spread: {ESP} (-1.5)", ESP)
        b = _resolve(_board(), S_ESP.replace("home", "away"), f"Spread: {ESP} (-1.5)", ESP)
        assert _short(a) == _short(b)


class TestSpreadRefusals:
    def test_the_line_must_be_listed_byte_for_byte(self, armed):
        # S.C. State -19.5: the venue lists 21.5/22.5/24.5, never 19.5
        rows = _board()
        slug = f"cfb-scarst-flam-{D}-spread-away-19pt5"
        assert _resolve(rows, slug, "Spread: South Carolina State (-19.5)", "South Carolina State") is None
        ex = _explain(rows, slug, "Spread: South Carolina State (-19.5)", "South Carolina State")
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:line-absent")
        # and the nearest listed line is never taken
        assert _resolve(rows, f"cfb-scarst-flam-{D}-spread-away-22pt5",
                        "Spread: South Carolina State (-22.5)", "South Carolina State") is not None

    def test_nicknames_on_the_venue_row_are_unreadable(self, armed):
        rows = _board()
        for ln in ("21pt5", "23pt5", "24pt5"):
            L = ln.replace("pt", ".")
            slug = f"cfb-washst-wash-{D}-spread-home-{ln}"
            assert _resolve(rows, slug, f"Spread: Washington (-{L})", "Washington State") is None
            ex = _explain(rows, slug, f"Spread: Washington (-{L})", "Washington State")
            assert ex["split"] == "spread:names-unreadable" and ex["c3"]["venue_names"] == ["cougars", "huskies"]

    def test_the_schools_on_the_venue_row_are_readable(self, armed):
        # the 22.5 row names the schools: S.C. State -22.5 -> neg yes
        h = _resolve(_board(), f"cfb-scarst-flam-{D}-spread-away-22pt5",
                     "Spread: South Carolina State (-22.5)", "South Carolina State")
        assert _short(h) == (f"asc-cfb-scarst-flam-{D}-neg-22pt5", "yes", LONG, "premap_spread", None)
        # Florida A&M -22.5 would be pos-22.5 NO, and the venue lists no pos row: line-absent
        ex = _explain(_board(), f"cfb-scarst-flam-{D}-spread-home-22pt5",
                      "Spread: Florida A&M (-22.5)", "Florida A&M")
        assert ex["split"] == "spread:line-absent" and ex["c3"]["wanted"] == f"asc-cfb-scarst-flam-{D}-pos-22pt5"

    def test_whole_number_lines_refuse(self, armed):
        ex = _explain(_board(), f"lal-esp-sev-{D}-spread-home-2", f"Spread: {ESP} (-2)", ESP)
        assert ex["split"] == "spread:whole-number"
        assert _resolve(_board(), f"lal-esp-sev-{D}-spread-home-2", f"Spread: {ESP} (-2)", ESP) is None

    def test_the_title_must_carry_his_slugs_line_and_a_readable_team(self, armed):
        rows = _board()
        assert _explain(rows, S_ESP, f"Spread: {ESP} (-2.5)", ESP)["split"] == "spread:title-shear"
        assert _explain(rows, S_ESP, "Spread: Real Betis (-1.5)", ESP)["split"] == "spread:title-unreadable"
        assert _explain(rows, S_ESP, f"{ESP} vs. {SEV}", ESP)["split"] == "spread:title-shape"
        assert _explain(rows, S_ESP, f"Spread: {ESP} (-1.5)", "Yes")["split"] == "spread:outcome"

    def test_segments_never_cross(self, armed):
        rows = _board()
        # only the first-half row at his line: the full-game slug refuses
        only_fh = [r for r in rows if not (r["identifier"].startswith("asc-lal-esp-sev")
                                           and "-fh-" not in r["identifier"]
                                           and "-sh-" not in r["identifier"])]
        assert _resolve(only_fh, S_ESP, f"Spread: {ESP} (-1.5)", ESP) is None
        # a first-half spread slug maps to the -fh- row and nothing else
        h = _resolve(rows, f"lal-esp-sev-{D}-first-half-spread-home-1pt5",
                     f"1st Half Spread: {ESP} (-1.5)", ESP)
        assert _short(h) == (f"asc-lal-esp-sev-{D}-fh-neg-1pt5", "yes", LONG, "premap_spread", None)
        no_fh = [r for r in rows if "-fh-" not in r["identifier"]]
        ex = _explain(no_fh, f"lal-esp-sev-{D}-first-half-spread-home-1pt5",
                      f"1st Half Spread: {ESP} (-1.5)", ESP)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "spread:segment-absent"
        # a full-game title on a first-half slug is shear
        assert _explain(rows, f"lal-esp-sev-{D}-first-half-spread-home-1pt5",
                        f"Spread: {ESP} (-1.5)", ESP)["split"] == "spread:title-segment"

    def test_the_intent_is_the_venues_own(self, armed):
        rows = [dict(r, intent=SHORT) if r["identifier"] == f"asc-lal-esp-sev-{D}-neg-1pt5"
                and r["side_norm"] == "yes" else r for r in _board()]
        assert _resolve(rows, S_ESP, f"Spread: {ESP} (-1.5)", ESP) is None
        assert _explain(rows, S_ESP, f"Spread: {ESP} (-1.5)", ESP)["split"] == "spread:intent"

    def test_two_league_codes_on_his_suffix_refuse(self, armed):
        twin = [(ev, dict(m, slug=m["slug"].replace("asc-lal-", "asc-lalw-")))
                for ev, m in _venue() if m["slug"].startswith("asc-lal-esp-sev")]
        for _ev, m in twin:
            for s in m["marketSides"]:
                s["identifier"] = m["slug"]
        rows = _board(extra=twin)
        ex = _explain(rows, f"lal2-esp-sev-{D}-spread-home-1pt5", f"Spread: {ESP} (-1.5)", ESP,
                      ev=f"{ESP} vs. {SEV}")
        assert ex["split"] == "spread:league-ambiguous" and ex["c3"]["league_codes"] == ["lal", "lalw"]
        assert _resolve(rows, f"lal2-esp-sev-{D}-spread-home-1pt5", f"Spread: {ESP} (-1.5)", ESP,
                        ev=f"{ESP} vs. {SEV}") is None


# ----------------------------------------------- 3. first-half totals

class TestFirstHalfTotals:
    def test_his_first_half_total_maps_to_the_fh_row_only(self, armed):
        rows = _board()
        h = _resolve(rows, f"sea-juv-mil-{D}-first-half-total-0pt5",
                     "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under")
        assert _short(h) == (f"tsc-sea-juv-mil-{D}-fh-0pt5", "under", SHORT, "premap_fh_total", None)
        h = _resolve(rows, f"lal-esp-sev-{D}-first-half-total-0pt5",
                     f"{ESP} vs. {SEV}: 1st Half O/U 0.5", "Over")
        assert _short(h) == (f"tsc-lal-esp-sev-{D}-fh-0pt5", "over", LONG, "premap_fh_total", None)

    def test_never_the_full_game_line(self, armed):
        # the venue lists the full game only: the segment is absent, and
        # the explain keeps the family-gap step (the 2026-09-03 pin)
        rows = [r for r in _board() if "-fh-" not in r["identifier"]]
        slug = f"sea-juv-mil-{D}-first-half-total-0pt5"
        assert _resolve(rows, slug, "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under") is None
        ex = _explain(rows, slug, "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under")
        assert (ex["step"], ex["split"]) == ("unknown_market_type", "total:segment-absent")

    def test_the_full_game_slug_never_takes_a_segment_row(self, armed):
        rows = [r for r in _board() if not (r["identifier"].startswith("tsc-lg1-olm-pfc")
                                            and "-fh-" not in r["identifier"]
                                            and "-sh-" not in r["identifier"])]
        slug = f"fl1-olm-pfc-{D}-total-1pt5"
        assert _resolve(rows, slug, "Olympique de Marseille vs. Paris FC: O/U 1.5", "Under") is None
        # and with the full row present the full game maps -- the wording
        # arm's own row, now unique because the segments are filtered
        h = _resolve(_board(), slug, "Olympique de Marseille vs. Paris FC: O/U 1.5", "Under")
        assert h["market_slug"] == f"tsc-lg1-olm-pfc-{D}-1pt5" and h["intent"] == SHORT

    def test_a_line_the_venue_does_not_list_stays_unmapped(self, armed):
        for ln in ("4pt5", "5pt5"):
            slug = f"fl1-olm-pfc-{D}-total-{ln}"
            t = f"Olympique de Marseille vs. Paris FC: O/U {ln.replace('pt', '.')}"
            assert _resolve(_board(), slug, t, "Under") is None
            assert _explain(_board(), slug, t, "Under")["split"] == "total:line-absent"

    def test_the_title_must_say_the_segment_and_the_line(self, armed):
        slug = f"sea-juv-mil-{D}-first-half-total-0pt5"
        assert _explain(_board(), slug, "Juventus FC vs. AC Milan: O/U 0.5", "Under")["split"] == "total:title-shear"
        assert _explain(_board(), slug, "Juventus FC vs. AC Milan: 1st Half O/U 1.5", "Under")["split"] == "total:title-shear"
        assert _explain(_board(), slug, "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Yes")["split"] == "total:outcome"

    def test_the_question_must_name_his_game(self, armed):
        rows = [dict(r, question="Will the total in BOL vs SAS be more than 0.5?")
                if r["identifier"] == f"tsc-sea-juv-mil-{D}-fh-0pt5" else r for r in _board()]
        ex = _explain(rows, f"sea-juv-mil-{D}-first-half-total-0pt5",
                      "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under")
        assert ex["split"] == "total:names"


# --------------------------------------------------------------- 4. btts

class TestBtts:
    def test_yes_and_no(self, armed):
        rows = _board()
        y = _resolve(rows, f"sea-juv-mil-{D}-btts", "Juventus FC vs. AC Milan: Both Teams to Score", "Yes")
        n = _resolve(rows, f"sea-juv-mil-{D}-btts", "Juventus FC vs. AC Milan: Both Teams to Score", "No")
        assert _short(y) == (f"astatc-sea-juv-mil-{D}-btts", "yes", LONG, "premap_btts", None)
        assert _short(n) == (f"astatc-sea-juv-mil-{D}-btts", "no", SHORT, "premap_btts", None)
        e = _resolve(rows, f"lal-esp-sev-{D}-btts", f"{ESP} vs. {SEV}: Both Teams to Score", "Yes")
        assert _short(e) == (f"astatc-lal-esp-sev-{D}-btts", "yes", LONG, "premap_btts", None)

    def test_under_the_league_alias(self, armed):
        h = _resolve(_board(), f"fl1-olm-pfc-{D}-btts",
                     "Olympique de Marseille vs. Paris FC: Both Teams to Score", "Yes")
        assert _short(h) == (f"astatc-lg1-olm-pfc-{D}-btts", "yes", LONG, "premap_btts", "fl1->lg1")

    def test_the_first_half_only_when_his_slug_says_so(self, armed):
        rows = _board()
        h = _resolve(rows, f"sea-juv-mil-{D}-first-half-btts",
                     "Juventus FC vs. AC Milan: 1st Half Both Teams to Score", "Yes")
        assert _short(h) == (f"astatc-sea-juv-mil-{D}-fh-btts", "yes", LONG, "premap_fh_btts", None)
        # his plain btts never takes the fh row
        no_full = [r for r in rows if r["identifier"] != f"astatc-sea-juv-mil-{D}-btts"]
        assert _resolve(no_full, f"sea-juv-mil-{D}-btts",
                        "Juventus FC vs. AC Milan: Both Teams to Score", "Yes") is None
        # a first-half title on the plain slug is shear
        assert _explain(rows, f"sea-juv-mil-{D}-btts",
                        "Juventus FC vs. AC Milan: 1st Half Both Teams to Score", "Yes")["split"] == "btts:title-shear"

    def test_the_question_must_name_his_two_clubs_on_his_date(self, armed):
        rows = _board()
        other = [dict(r, question=r["question"].replace("Juventus FC", "Bologna FC 1909"))
                 if r["identifier"] == f"astatc-sea-juv-mil-{D}-btts" else r for r in rows]
        ex = _explain(other, f"sea-juv-mil-{D}-btts", "Juventus FC vs. AC Milan: Both Teams to Score", "Yes")
        assert ex["split"] == "btts:names"
        dated = [dict(r, question=r["question"].replace(D, "2026-09-07"))
                 if r["identifier"] == f"astatc-sea-juv-mil-{D}-btts" else r for r in rows]
        ex = _explain(dated, f"sea-juv-mil-{D}-btts", "Juventus FC vs. AC Milan: Both Teams to Score", "Yes")
        assert ex["split"] == "btts:qdate"
        # the spurious '45' line the sweep stamps from the clock is not a bar
        assert all(r["line"] == "45" for r in rows if r["identifier"] == f"astatc-sea-juv-mil-{D}-btts")


# ------------------------------------------------ 5. the draw's witness

class TestTheDrawWitnessedByHisTitle:
    T_ESP = f"Will {ESP} vs. {SEV} end in a draw?"

    def test_no_event_title_his_own_title_is_the_witness(self, armed):
        h = _resolve(_board(), f"lal-esp-sev-{D}-draw", self.T_ESP, "Yes")
        assert _short(h) == (f"atc-lal-esp-sev-{D}-draw", "yes", LONG, "premap_draw_title", None)
        n = _resolve(_board(), f"lal-esp-sev-{D}-draw", self.T_ESP, "No")
        assert _short(n) == (f"atc-lal-esp-sev-{D}-draw", "no", SHORT, "premap_draw_title", None)

    def test_under_the_alias_and_the_key_builder(self, armed):
        # fl1-olm-pfc-…-draw was no_key_intersection: the draw title's
        # keys are its matchup now
        t = "Will Olympique de Marseille vs. Paris FC end in a draw?"
        keys = premap.event_keys_for(t, f"fl1-olm-pfc-{D}-draw")
        assert f"olympique de marseille vs paris fc@{D}" in keys
        assert not any("draw" in k for k in keys)
        h = _resolve(_board(), f"fl1-olm-pfc-{D}-draw", t, "Yes")
        assert _short(h) == (f"atc-lg1-olm-pfc-{D}-draw", "yes", LONG, "premap_draw_title", "fl1->lg1")

    def test_the_identity_only_caller_maps_the_draw_and_never_aliases(self):
        rows = _board()
        got = premap.yn_identity_rows(rows, "Yes", self.T_ESP, f"lal-esp-sev-{D}-draw")
        assert [r["identifier"] for r in got] == [f"atc-lal-esp-sev-{D}-draw"]
        assert premap.yn_identity_rows(rows, "Yes", "Will Olympique de Marseille vs. Paris FC end in a draw?",
                                       f"fl1-olm-pfc-{D}-draw") == []

    def test_the_event_title_still_wins_when_present(self, armed):
        h = _resolve(_board(), f"lal-esp-sev-{D}-draw", self.T_ESP, "Yes", ev=f"{ESP} vs. {SEV}")
        assert h["matched_by"] == "premap_identity"

    def test_only_the_attested_wording_is_a_witness(self, armed):
        rows = _board()
        # the C2 pin: a dated title with no event title stays unwitnessed
        ex = _explain(rows, f"lal-esp-sev-{D}-draw", f"Will {ESP} vs. {SEV} end in a draw on {D}?", "Yes")
        assert ex["split"] == "yn:draw-unwitnessed"
        for t in ("Will the match end in a draw?", f"Will {ESP} vs. {SEV} end in a draw at halftime?",
                  f"{ESP} vs. {SEV}: Draw", f"Will {ESP} vs. {SEV} B end in a draw?"):
            assert _resolve(rows, f"lal-esp-sev-{D}-draw", t, "Yes") is None, t
            assert _explain(rows, f"lal-esp-sev-{D}-draw", t, "Yes")["split"] == "yn:draw-unwitnessed", t

    def test_the_venues_draw_row_must_name_his_two_sides(self, armed):
        rows = _board()
        ex = _explain(rows, f"lal-esp-sev-{D}-draw", "Will Real Betis vs. Sevilla FC end in a draw?", "Yes")
        assert ex["split"] == "yn:draw-names"
        assert _resolve(rows, f"lal-esp-sev-{D}-draw", "Will Real Betis vs. Sevilla FC end in a draw?", "Yes") is None


# ------------------------------------------------ 6. the shared stem

class TestTheSharedStem:
    S = f"cfb-washst-wash-{D}"

    def test_code_names_reads_the_outcomes_own_words(self):
        assert map_lane.shared_stem("washst", "wash") == "wash" and map_lane.shared_stem("mich", "mist") == ""
        assert map_lane.code_names("washst", "Washington State", "wash")
        assert not map_lane.code_names("wash", "Washington State", "washst")
        assert map_lane.code_names("wash", "Washington", "washst")
        assert not map_lane.code_names("washst", "Washington", "wash")
        assert map_lane.code_names("wash", "Washington Huskies", "washst")
        assert map_lane.code_names("michst", "Michigan State", "mich")
        assert not map_lane.code_names("mich", "Michigan State", "michst")
        # no stem: code_hits exactly (the C1 pins)
        assert map_lane.code_names("mich", "Michigan State", "mist") is map_lane.code_hits("mich", "Michigan State")

    def test_the_pair_agrees_both_ways(self):
        assert map_lane.pair_agrees(self.S, 1, "Washington State", 0) is None
        assert map_lane.pair_agrees(self.S, 0, "Washington", 1) is None
        M = f"cfb-michst-mich-{D}"
        assert map_lane.pair_agrees(M, 0, "Michigan", 1) is None
        assert map_lane.pair_agrees(M, 1, "Michigan State", 0) is None
        assert map_lane.pair_agrees(f"cfb-mich-michst-{D}", 0, "Michigan State", 1) is None

    def test_a_genuine_conflict_still_refuses(self):
        assert map_lane.pair_agrees("cfb-bayl-aubrn-2026-09-05", 0, "Baylor", 1) == "side_code_conflict"
        # mich/mist share no stem: the sibling 'Michigan State' names the
        # mapped 'mich' by its first word and not its own 'mist' -- conflict
        assert map_lane.pair_agrees("cfb-mich-mist-2026-09-05", 0, "Michigan State", 1) == "side_code_conflict"
        # a sibling that names the MAPPED team under a shared stem is still
        # a conflict ('Washington' beside a mapped 'wash'); a sibling on the
        # same index is the pair rule's own refusal
        assert map_lane.pair_agrees(self.S, 1, "Washington", 0) == "side_code_conflict"
        assert map_lane.pair_agrees(self.S, 1, "Washington State", 1) == "side_code_pair"

    def test_both_tokens_map_by_their_own_words(self):
        aec = "aec-" + self.S
        mk = {"slug": aec, "closed": False, "question": "Washington State vs. Washington",
              "marketSides": [{"identifier": aec, "description": "Cougars", "long": True},
                              {"identifier": aec, "description": "Huskies", "long": False}]}
        hit, why = map_lane.aec_code_side(self.S, "Washington State", mk, 0)
        assert why is None and hit["outcome"] == "Cougars" and hit["intent"] == LONG
        hit, why = map_lane.aec_code_side(self.S, "Washington", mk, 1)
        assert why is None and hit["outcome"] == "Huskies" and hit["intent"] == SHORT
        assert map_lane.aec_code_side(self.S, "Washington State", mk, 1)[1] == "side_code_conflict"
        assert map_lane.aec_code_side(self.S, "Washington", mk, 0)[1] == "side_code_conflict"


# --------------------------------- 7. families the venue does not list

class TestFamiliesTheVenueDoesNotList:
    @pytest.mark.parametrize("slug,title,outcome,fam,name", [
        (f"sea-juv-mil-{D}-exact-score-2-0", "Exact Score: Juventus FC 2 - 0 AC Milan?", "No",
         "exact_score", "exact:family-absent"),
        (f"sea-juv-mil-{D}-exact-score-0-2", "Exact Score: Juventus FC 0 - 2 AC Milan?", "Yes",
         "exact_score", "exact:family-absent"),
        (f"fl1-olm-pfc-{D}-halftime-result-draw", "Olympique de Marseille vs. Paris FC: Draw at halftime?",
         "Yes", "halftime_result", "halftime:family-absent"),
        (f"lal-esp-sev-{D}-team-total-home-2pt5", f"{ESP} vs. {SEV}: {ESP} O/U 2.5", "Under",
         "team_total", "team_total:family-absent"),
    ])
    def test_refused_by_family_name_never_mapped(self, armed, slug, title, outcome, fam, name):
        # even with an es-/ht-/tt- shaped row on the board (fail closed:
        # the venue's grammar for these families is unverified)
        extra = [("aec-sea-juv-mil-2026-09-06", _yn(f"astatc-sea-juv-mil-{D}-es-2-0",
                                                    "Will JUV vs MIL finish 2-0?")),
                 ("aec-sea-juv-mil-2026-09-06", _yn(f"astatc-sea-juv-mil-{D}-es-0-2",
                                                    "Will JUV vs MIL finish 0-2?"))]
        rows = _board(extra=extra)
        assert family_of(slug) == fam
        assert _resolve(rows, slug, title, outcome) is None
        ex = _explain(rows, slug, title, outcome)
        assert ex["step"] == "unknown_market_type" and ex["split"] == "family_not_listed"
        assert ex["family"] == fam and ex["refusal"] == name

    def test_the_readers(self):
        assert (family_of(f"sea-juv-mil-{D}-first-half-total-0pt5"), segment_of(f"sea-juv-mil-{D}-first-half-total-0pt5")) == ("total", "fh")
        assert (family_of(f"x-a-b-{D}-second-half-total-1pt5"), segment_of(f"x-a-b-{D}-second-half-total-1pt5")) == ("total", "sh")
        assert family_of(f"x-a-b-{D}-spread-home-1pt5") == "spread" and family_of(f"x-a-b-{D}-btts") == "btts"
        assert family_of(f"x-a-b-{D}-a") == "moneyline" and family_of(f"x-a-b-{D}") == "moneyline"
        assert family_of("lmx-ame-san-2026-08-29-fh") == "prop" and segment_of("lmx-ame-san-2026-08-29-fh") == ""
        assert family_of(f"x-a-b-{D}-weird-thing") == "unknown"
        assert premap.c3_his(f"x-a-b-{D}-spread-home-pos-1pt5") is None
        assert premap.c3_his(f"x-a-a-{D}-spread-home-1pt5") is None


# ---------------------------------------------- 8. the census, purity

class TestTheCensusAndTheSeams:
    def test_explain_unmapped_prints_the_family_refusal(self, armed):
        class _P(_Pool):
            async def fetch(self, sql, *a):
                if "us_premap" in sql:
                    return await super().fetch(sql, *a)
                return []

        p = _P(_board())
        ctx = {"title": "Spread: South Carolina State (-19.5)", "event_title": None,
               "outcome": "South Carolina State", "his_slug": f"cfb-scarst-flam-{D}-spread-away-19pt5"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "no_side_match:spread:line-absent"
        ctx = {"title": "Juventus FC vs. AC Milan: Both Teams to Score", "event_title": None,
               "outcome": "Yes", "his_slug": f"sea-juv-mil-{D}-btts"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "resolves"
        ctx = {"title": "Exact Score: Juventus FC 2 - 0 AC Milan?", "event_title": None,
               "outcome": "No", "his_slug": f"sea-juv-mil-{D}-exact-score-2-0"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "unknown_market_type:family_not_listed"

    def test_the_pick_is_pure_and_the_source_is_premap(self):
        for fn in (premap.c3_pick, premap._c3_pick_spread, premap._c3_pick_total,
                   premap._c3_pick_btts, premap._yn_draw_title_sides, premap._asc_yes_no):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "SequenceMatcher"):
                assert forbidden not in src, (fn.__name__, forbidden)
        # every C3 label is a premap class: the quarantine's resume class
        assert {"premap_spread", "premap_total", "premap_fh_total", "premap_btts",
                "premap_fh_btts", "premap_draw_title"} <= set(
            f"premap_{x}" for x in ("spread", "total", "fh_total", "btts", "fh_btts", "draw_title"))

    def test_the_mirror_maps_them_as_premap(self, armed, monkeypatch):
        """map_market's premap step answers these rows: source 'premap'."""
        rows = _board()

        class _P(_Pool):
            async def fetch(self, sql, *a):
                if "us_premap" in sql:
                    return await super().fetch(sql, *a)
                return []

        fills = [{"asset": "tok-a", "market_slug": S_ESP, "market_title": f"Spread: {ESP} (-1.5)",
                  "event_title": None, "outcome": ESP, "outcome_index": 0, "side": "BUY", "size": 10.0,
                  "price": 0.5}]
        m = asyncio.run(ms.map_market(_P(rows), fills))
        assert m and m["source"] == "premap" and m["us_slug"] == f"asc-lal-esp-sev-{D}-neg-1pt5"


# ------------------------------------ 9. review fold (2026-09-06 21:2xZ)

@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


class TestTheSwitchHoldsAllOfC3:
    """Review major 1: spreads, first-half totals, btts and the draw's
    title witness all sit behind PREMAP_YN_IDENTITY, the switch C2's
    identity arm answers to. Off, every one of them reads exactly as
    it did before C3."""

    def test_off_the_spread_is_no_side_match(self, dark):
        rows = _board()
        assert _resolve(rows, S_ESP, f"Spread: {ESP} (-1.5)", ESP) is None
        ex = _explain(rows, S_ESP, f"Spread: {ESP} (-1.5)", ESP)
        assert ex["step"] == "no_side_match" and "c3" not in ex and ex["c3_on"] is False
        assert not str(ex.get("split") or "").startswith("spread:")

    def test_off_the_first_half_total_is_the_prop_family_gap(self, dark):
        rows = _board()
        slug = f"sea-juv-mil-{D}-first-half-total-0pt5"
        assert _resolve(rows, slug, "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under") is None
        ex = _explain(rows, slug, "Juventus FC vs. AC Milan: 1st Half O/U 0.5", "Under")
        assert (ex["step"], ex["split"]) == ("unknown_market_type", "family_not_listed")

    def test_off_btts_is_the_pre_c3_refusal(self, dark):
        rows = _board()
        t = "Juventus FC vs. AC Milan: Both Teams to Score"
        assert _resolve(rows, f"sea-juv-mil-{D}-btts", t, "Yes") is None
        ex = _explain(rows, f"sea-juv-mil-{D}-btts", t, "Yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:title-title_not_win_shape"

    def test_off_the_draw_witness_is_off(self, dark):
        rows = _board()
        t = f"Will {ESP} vs. {SEV} end in a draw?"
        assert _resolve(rows, f"lal-esp-sev-{D}-draw", t, "Yes") is None
        assert _resolve(rows, f"fl1-olm-pfc-{D}-draw",
                        "Will Olympique de Marseille vs. Paris FC end in a draw?", "Yes") is None
        assert premap.match_side(rows, "Yes", t, f"lal-esp-sev-{D}-draw") is None

    def test_on_they_map_and_the_explain_says_the_switch(self, armed):
        assert _explain(_board(), S_ESP, f"Spread: {ESP} (-1.5)", ESP)["c3_on"] is True
        assert _resolve(_board(), S_ESP, f"Spread: {ESP} (-1.5)", ESP) is not None

    def test_the_c3_call_sits_inside_the_identity_block(self):
        for fn in (premap.resolve, premap.resolve_explain):
            src = inspect.getsource(fn)
            i = src.index("if hit is None and yn_identity_on():")
            j = src.index("c3_pick(kept, outcome, market_title, global_slug")
            assert i < j
            # no C3 call before the switch, and c3_his is read through it
            assert "c3_pick(" not in src[:i]
            assert "c3_his(global_slug) if yn_identity_on() else None" in src


class TestTheAscConventionIsObserved:
    """Review major 2a: the asc LONG side is OBSERVED, not inferred --
    venue_asc_2125.log (21:26:43Z) and venue_asc_2130.log (21:29:29Z),
    the gateway's bbo/book on one live game (South Carolina State, the
    ~19.5-point favorite, vs Florida A&M), the same game's two lines
    read together:

      asc-cfb-scarst-flam-…-neg-24pt5  'Will the Bulldogs cover -24.5'
          LONG 0.18 (bid 0.17 / ask 0.20), pre-game close 0.24, shortPx 0.82
      asc-cfb-scarst-flam-…-pos-21pt5  'Will the Bulldogs cover 21.5'
          LONG 0.99 (bid 0.99 x 859,957), pre-game close 0.99, shortPx 0.01
      asc-cfb-washst-wash-…-neg-24pt5  'Will the Cougars cover -24.5'
          LONG 0.01, shortPx 0.99 (the underdog covering -24.5)
      asc-lg1-olm-pfc-…-neg-1pt5       'Marseille cover -1.5'
          opened 0.27, settled 0.0000 (Marseille did not win by two);
          asc-lal-esp-sev-…-neg-1pt5 settled 0.0000 the same way

    The favorite covering +21.5 is near-certain and its LONG trades at
    0.99; the favorite covering -24.5 is unlikely and its LONG at
    0.18-0.24: were LONG the complement the two prices would be
    swapped. LONG = the named (first-slug) team covers the stated line
    = the parser's 'yes' row; SHORT = it does not = 'no'. The gateway
    serves no per-market payload (GET /v1/markets/<slug> is 404 for
    every family), so the side names come only from the events/markets
    listing the poller parses (marketSides sharing the slug, described
    by the line, marked long/short) -- the shape _market_rows reads."""

    OBSERVED = {
        # identifier: (question, pre-game close, LONG px, shortPx)
        "asc-cfb-scarst-flam-2026-09-06-neg-24pt5": (
            "Will the Bulldogs cover -24.5 vs the Rattlers in Bulldogs vs. Rattlers?", 0.24, 0.18, 0.82),
        "asc-cfb-scarst-flam-2026-09-06-pos-21pt5": (
            "Will the Bulldogs cover 21.5 vs the Rattlers in Bulldogs vs. Rattlers?", 0.99, 0.99, 0.01),
        "asc-cfb-washst-wash-2026-09-06-neg-24pt5": (
            "Will the Cougars cover -24.5 vs the Huskies in Cougars vs. Huskies?", 0.01, 0.01, 0.99),
        "asc-lg1-olm-pfc-2026-09-06-neg-1pt5": (
            "Will the Olympique de Marseille cover -1.5 vs the Paris FC in OLM vs PFC?", 0.27, 0.0, 1.0),
    }

    def test_the_long_side_is_the_covers_side(self):
        fav_neg = self.OBSERVED["asc-cfb-scarst-flam-2026-09-06-neg-24pt5"]
        fav_pos = self.OBSERVED["asc-cfb-scarst-flam-2026-09-06-pos-21pt5"]
        # the favorite: LONG on +21.5 is near-certain, LONG on -24.5 is not
        assert fav_pos[2] > 0.9 > 0.5 > fav_neg[2]
        for ident, (q, _close, long_px, short_px) in self.OBSERVED.items():
            assert abs(long_px + short_px - 1.0) < 0.05
            rows = premap._market_rows({"slug": "e", "title": "t"}, _asc(ident, q))
            by = {r["side_norm"]: r["intent"] for r in rows}
            assert by == {"yes": LONG, "no": SHORT}, ident
            # and the yes row IS the LONG marker's row, never the other
            assert {r["side_norm"] for r in rows if r["intent"] == LONG} == {"yes"}

    def test_the_settled_rows_agree(self):
        # btts LONG settled 1 (both scored) -> LONG is 'yes'; fh total LONG
        # settled 1 (over 1.5) -> LONG is 'over'; the parser's rows say so
        rows = premap._market_rows({"slug": "e", "title": "t"}, _yn(
            f"astatc-lg1-olm-pfc-{D}-btts",
            "Will both teams score in the match between Olympique de Marseille and Paris FC on 2026-09-06 2:45PM ET?"))
        assert {r["side_norm"]: r["intent"] for r in rows} == {"yes": LONG, "no": SHORT}
        rows = premap._market_rows({"slug": "e", "title": "t"}, _tsc(
            f"tsc-lg1-olm-pfc-{D}-fh-1pt5", "Will the total in OLM vs PFC be more than 1.5?"))
        assert {r["side_norm"]: r["intent"] for r in rows} == {"over": LONG, "under": SHORT}


class _EchoPool:
    def __init__(self, parent):
        self.parent = parent
        self.writes: list = []

    async def fetchrow(self, sql, *a):
        assert "us_premap" in sql
        return {"event_slug": "e", "market_slug": self.parent}

    async def fetchval(self, sql, *a):
        return None

    async def execute(self, sql, *a):
        self.writes.append((sql, a))


class TestTheEchoReDerivesTheFamilies:
    """Review major 2b: the post-fill side echo re-derives a spread /
    btts / fh-total fill through c3_pick on the venue's live rows of
    the market we bought, and compares identifier AND intent."""

    def _echo(self, monkeypatch, market, us_slug, outcome, title, his_slug, intent):
        from sportsassets import live_executor as le

        rows = premap._market_rows({"slug": "", "title": ""}, market)
        monkeypatch.setattr(premap, "live_rows_for_market", lambda parent: rows)

        async def _indep(*a, **k):
            return "unverified", "skipped in test"
        monkeypatch.setattr(le, "_independent_check", _indep)
        pool = _EchoPool(market["slug"])
        v = asyncio.run(le._side_echo_verify(pool, 1, us_slug, outcome, title, attempts=1, shadow=True,
                                             his_slug=his_slug, intent=intent, mapping_src="premap"))
        return v, pool

    def test_a_spread_fill_on_the_right_side_passes_and_the_complement_fails(self, monkeypatch, armed):
        m = _asc(f"asc-lal-esp-sev-{D}-neg-1pt5", f"Will the {ESP} cover -1.5 vs the {SEV} in ESP vs SEV?")
        v, _ = self._echo(monkeypatch, m, m["slug"], ESP, f"Spread: {ESP} (-1.5)", S_ESP, LONG)
        assert v == "ok"
        v, _ = self._echo(monkeypatch, m, m["slug"], SEV, f"Spread: {ESP} (-1.5)", S_ESP, SHORT)
        assert v == "ok"
        # the complement: we sent LONG for his Sevilla (+1.5) side
        v, pool = self._echo(monkeypatch, m, m["slug"], SEV, f"Spread: {ESP} (-1.5)", S_ESP, LONG)
        assert v == "mismatch"
        v, _ = self._echo(monkeypatch, m, m["slug"], ESP, f"Spread: {ESP} (-1.5)", S_ESP, SHORT)
        assert v == "mismatch"
        # shadow: counted, never tripped
        assert not any("side_echo_tripped" in sql for sql, _ in pool.writes)
        assert any("side_echo_shadow" in a for _, a in pool.writes)

    def test_a_btts_and_a_first_half_total_fill(self, monkeypatch, armed):
        b = _yn(f"astatc-sea-juv-mil-{D}-btts",
                f"Will both teams score in the match between Juventus FC and AC Milan on {D} 2:45PM ET?")
        t = "Juventus FC vs. AC Milan: Both Teams to Score"
        assert self._echo(monkeypatch, b, b["slug"], "Yes", t, f"sea-juv-mil-{D}-btts", LONG)[0] == "ok"
        assert self._echo(monkeypatch, b, b["slug"], "No", t, f"sea-juv-mil-{D}-btts", SHORT)[0] == "ok"
        assert self._echo(monkeypatch, b, b["slug"], "Yes", t, f"sea-juv-mil-{D}-btts", SHORT)[0] == "mismatch"
        f = _tsc(f"tsc-sea-juv-mil-{D}-fh-0pt5", "Will the total in JUV vs MIL be more than 0.5?")
        t = "Juventus FC vs. AC Milan: 1st Half O/U 0.5"
        slug = f"sea-juv-mil-{D}-first-half-total-0pt5"
        assert self._echo(monkeypatch, f, f["slug"], "Under", t, slug, SHORT)[0] == "ok"
        assert self._echo(monkeypatch, f, f["slug"], "Under", t, slug, LONG)[0] == "mismatch"
        # a fill on the full-game row for a first-half slug: the pick names
        # no row on that market (segment identity), never certified
        g = _tsc(f"tsc-sea-juv-mil-{D}-0pt5", "Will the total in JUV vs MIL be more than 0.5?")
        assert self._echo(monkeypatch, g, g["slug"], "Under", t, slug, SHORT)[0] == "unverified"

    def test_the_independent_check_never_crosses_families(self, monkeypatch):
        """A premap spread fill must not be cross-checked against the
        moneyline candidates (a different identifier would read as a
        mismatch off a correct order)."""
        from sportsassets import live_executor as le
        from sportsassets import pmus

        def _boom(*a, **k):
            raise AssertionError("moneyline resolver consulted for a spread")
        monkeypatch.setattr(pmus, "resolve_market_exact", _boom)
        monkeypatch.setattr(pmus, "resolve_derivative_exact", lambda *a, **k: None)
        v, why = asyncio.run(le._independent_check(f"asc-lal-esp-sev-{D}-neg-1pt5", ESP,
                                                   f"Spread: {ESP} (-1.5)", S_ESP, LONG, "premap"))
        assert v == "unverified" and "derivative" in why
        v, why = asyncio.run(le._independent_check(f"astatc-sea-juv-mil-{D}-btts", "Yes",
                                                   "Juventus FC vs. AC Milan: Both Teams to Score",
                                                   f"sea-juv-mil-{D}-btts", LONG, "premap"))
        assert v == "unverified" and "btts" in why
        monkeypatch.setattr(pmus, "resolve_derivative_exact",
                            lambda *a, **k: {"market_slug": f"asc-lal-esp-sev-{D}-pos-1pt5", "intent": LONG})
        v, _ = asyncio.run(le._independent_check(f"asc-lal-esp-sev-{D}-neg-1pt5", ESP,
                                                 f"Spread: {ESP} (-1.5)", S_ESP, LONG, "premap"))
        assert v == "mismatch"


class TestTheStemAndTheWholeFirstWord:
    """Review minor 3: 'Texas Tech' names 'texas' (its whole first word)
    and not 'tex'; 'Miami (OH)' names 'miami' and not 'mia'. A single-
    side hit therefore maps only with the pair rule agreeing: the
    sibling that claims the mapped code refuses the market."""

    @pytest.mark.parametrize("slug,long_team,short_team", [
        (f"cfb-tex-texas-{D}", "Texas", "Texas Tech"),
        (f"cfb-mia-miami-{D}", "Miami", "Miami (OH)"),
    ])
    def test_the_pair_rule_refuses_the_collision(self, slug, long_team, short_team):
        _lg, a, b, _d, _t = map_lane.slug_head(slug)
        assert map_lane.code_names(b, short_team, a) and not map_lane.code_names(a, short_team, b)
        assert map_lane.code_names(b, long_team, a) and not map_lane.code_names(a, long_team, b)
        aec = "aec-" + slug
        mk = {"slug": aec, "closed": False, "question": "X vs. Y",
              "marketSides": [{"identifier": aec, "description": "Side A", "long": True},
                              {"identifier": aec, "description": "Side B", "long": False}]}
        # the longer code's team maps on its own words (index 1)
        hit, why = map_lane.aec_code_side(slug, long_team, mk, 1)
        assert why is None and hit["side_index"] == 1
        # the shorter code's team names the LONGER code too: index 0 is a conflict
        assert map_lane.aec_code_side(slug, short_team, mk, 0)[1] == "side_code_conflict"
        # and beside the mapped token, that sibling claims the mapped code: refused
        assert map_lane.pair_agrees(slug, 1, short_team, 0) == "side_code_conflict"


class TestTheAliasCorroboration:
    """Review minor 4: the btts alias reads both clubs off the venue's
    question; an aliased fh-total's names come only through the event
    keys (the tsc question names codes) -- documented."""

    def test_an_aliased_btts_row_naming_other_clubs_refuses(self, armed):
        rows = [dict(r, question=r["question"].replace("Olympique de Marseille", "Paris Saint-Germain"))
                if r["identifier"] == f"astatc-lg1-olm-pfc-{D}-btts" else r for r in _board()]
        t = "Olympique de Marseille vs. Paris FC: Both Teams to Score"
        assert _resolve(rows, f"fl1-olm-pfc-{D}-btts", t, "Yes") is None
        assert _explain(rows, f"fl1-olm-pfc-{D}-btts", t, "Yes")["split"] == "btts:names"


class TestTheMirrorFamilyLabel:
    """Review minor 5: the mirror files a first-half total under
    'total' (its segment is in the slug); the copy lane's prop block is
    unchanged."""

    def test_the_labels(self):
        from sportsassets import copy_sports as cs

        fh = f"sea-juv-mil-{D}-first-half-total-0pt5"
        assert cs.mirror_family_of(fh) == "total" and cs.market_type_of(fh) == "prop"
        assert cs.segment_of(fh) == "fh"
        assert cs.copy_verdict("rn1", fh) == "market_type_blocked"
        assert cs.mirror_family_of(S_ESP) == "spread" and cs.mirror_family_of(f"sea-juv-mil-{D}-btts") == "btts"
        assert cs.mirror_family_of(f"sea-juv-mil-{D}-exact-score-2-0") == "exact_score"
        assert cs.mirror_family_of(f"lal-esp-sev-{D}-team-total-home-2pt5") == "prop"
        assert cs.mirror_family_of(f"sea-juv-mil-{D}-juv") == "moneyline"
        assert ms._family_of(fh) == "total"
        from sportsassets.analytics import mirror_live_rules as r
        assert "total" in r.MIRROR_FAMILIES

    def test_the_live_worker_reads_it(self):
        from sportsassets.workers import mirror_live
        src = inspect.getsource(mirror_live)
        assert "family=copy_sports.mirror_family_of(slug)" in src
        assert "family=copy_sports.market_type_of(slug)" not in src
