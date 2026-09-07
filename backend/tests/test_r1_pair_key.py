"""R1 (2026-09-07): the league-stripped dated PAIR KEY `<a>-<b>-<date>`,
emitted on both sides beside the slug key -- a lookup, never a decision.

His feed and the venue code the same game under different league codes
(nor/els, den/sld, sui/swsl, itsb/srb ...) with the team codes and the
date byte-identical, so the slug keys never met and a moneyline judged
before markets.event_title was stored read no_key_intersection. The els
rows below are the 19:09Z dump's, verbatim (test_c2_yesno_alias.VENUE);
every arm that reads the fetched rows is pinned unchanged: the alias
arm's witness rule, yn:league-ambiguous on two codes, C3's _c3_code.

Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import re

import pytest

from sportsassets.workers import premap
from tests.test_c2_yesno_alias import (EV_TRO, FEED, ID_ELS_TRO, S_TRO, T_TRO, VENUE, _market,
                                       _short)

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"
ELS = "aec-els-kbk-tro-2026-09-06"
PAIR = "kbk-tro-2026-09-06"
S_KBK, T_KBK = "nor-kbk-tro-2026-09-06-kbk", "Will Kristiansund BK win on 2026-09-06?"
ID_ELS_KBK = "atc-els-kbk-tro-2026-09-06-kbk"


def _board(venue: dict | None = None, *, pre_r1: bool = False) -> list[dict]:
    """Rows keyed as the sweep keys them (event keys + the row's own
    name keys); `pre_r1` keys them as the sweep did before this build
    (the event's keys without the pair key, no name keys) to prove the
    pair key is what fetches them."""
    rows: list[dict] = []
    for ev_slug, (ev_title, mkts) in (venue if venue is not None else VENUE).items():
        keys = premap.event_keys_for(ev_title, ev_slug)
        pair = premap._pair_key(ev_slug.split("-", 1)[1], D)
        for ident, q in mkts:
            for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, _market(ident, q)):
                r["event_keys"] = ([k for k in keys if k != pair] if pre_r1
                                   else premap.keys_for_row(keys, r))
                rows.append(r)
    return rows


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        assert "us_premap" in sql
        if "identifier ~" in sql:
            return [dict(r) for r in self.rows if re.search(a[0], r["identifier"])]
        k = set(a[0])
        return [dict(r) for r in self.rows if set(r["event_keys"]) & k]


def _resolve(rows, slug, title, ev, outcome):
    return asyncio.run(premap.resolve(_Pool(rows), title, ev, outcome, slug))


def _explain(rows, slug, title, ev, outcome):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title, ev, outcome, slug))


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# the venue's event title spelled otherwise than his: no matchup key meets
OTHERWISE = {ELS: ("Kristiansund vs. Tromsø", VENUE[ELS][1])}


# ------------------------------------------------- the key, both sides

class TestTheKeyIsEmittedOnBothSides:
    def test_7_sweep_side_the_venue_slug_keys_the_pair_and_nothing_shorter(self):
        for slug in (ELS, "atc-els-kbk-tro-2026-09-06-kbk", "els-kbk-tro-2026-09-06"):
            keys = premap.event_keys_for("Kristiansund BK vs. Tromso IL", slug)
            assert PAIR in keys, slug
            for k in keys:
                if "@" in k or " " in k:
                    continue
                toks = [t for t in k[:-len(D)].rstrip("-").split("-") if t] if k.endswith(D) else []
                assert len(toks) >= 2, (slug, k)     # never a bare date, never one team
            assert "tro-2026-09-06" not in keys and D not in keys

    def test_his_kindless_slug_keys_the_pair_beside_the_slug_key(self):
        keys = set(premap.event_keys_for(None, S_TRO))
        assert {PAIR, "nor-kbk-tro-2026-09-06"} <= keys
        assert PAIR in premap.event_keys_for(T_TRO, S_TRO)

    def test_the_two_sides_meet_on_the_pair_key_alone(self):
        his = set(premap.event_keys_for(T_KBK, S_KBK))
        venue = set(premap.event_keys_for("Kristiansund vs. Tromsø", ELS))
        assert his & venue == {PAIR}

    @pytest.mark.parametrize("slug,want", [
        ("gvs-2026-08-25", {"gvs-2026-08-25"}),
        ("atc-gvs-2026-08-25", {"atc-gvs-2026-08-25", "gvs-2026-08-25"}),
        ("a-b-c-d-2026-08-25", {"a-b-c-d-2026-08-25"}),
        ("aec-itfwo-2026-09-07-x", {"aec-itfwo-2026-09-07", "itfwo-2026-09-07"}),
        ("us-open-2026-winner", set()),
    ])
    def test_any_other_token_count_emits_no_pair_key(self, slug, want):
        """Two tokens have no league to strip; four are not the grammar;
        a bare date is never a key (the league_alias_probe's lesson)."""
        assert set(premap.event_keys_for(None, slug)) == want

    def test_6_dated_admissible_refuses_a_pair_key_without_a_date(self):
        assert premap._dated_admissible({"kbk-tro", "nor-kbk-tro"}, D) == set()
        assert premap._dated_admissible({PAIR, "kbk-tro"}, D) == {PAIR}


# ------------------------------------------------- the arms, unchanged

class TestTheFetchedRowsAnswerToTodaysArms:
    def test_1_no_event_title_the_rows_are_fetched_and_the_witness_rule_refuses(self, armed):
        rows = _board()
        assert _resolve(rows, S_KBK, T_KBK, None, "Yes") is None
        ex = _explain(rows, S_KBK, T_KBK, None, "Yes")
        assert ex["rows"] == 6 and (ex["step"], ex["split"]) == \
            ("no_side_match", "yn:alias-unwitnessed")
        # before R1 the keys never met: without the pair key on the rows
        ex0 = _explain(_board(pre_r1=True), S_KBK, T_KBK, None, "Yes")
        assert ex0["step"] == "no_key_intersection" and ex0["rows"] == 0

    def test_2_with_the_event_title_the_existing_alias_pin_holds(self, armed):
        rows = _board()
        slug, title, ev, oc = FEED[3]
        assert _short(_resolve(rows, slug, title, ev, oc)) == (ID_ELS_TRO, LONG, "premap_alias", "nor->els")
        assert _short(_resolve(rows, S_KBK, T_KBK, EV_TRO, "Yes")) == \
            (ID_ELS_KBK, LONG, "premap_alias", "nor->els")

    def test_3_the_venues_event_title_spelled_otherwise_maps_through_the_pair_key(self, armed):
        rows = _board(OTHERWISE)
        assert _short(_resolve(rows, S_KBK, T_KBK, EV_TRO, "Yes")) == \
            (ID_ELS_KBK, LONG, "premap_alias", "nor->els")
        assert _short(_resolve(rows, S_TRO, T_TRO, EV_TRO, "No")) == \
            (ID_ELS_TRO, SHORT, "premap_alias", "nor->els")
        h = _resolve(rows, "nor-kbk-tro-2026-09-06-draw",
                     "Will Kristiansund BK vs. Tromsø IL end in a draw?", EV_TRO, "Yes")
        assert _short(h) == ("atc-els-kbk-tro-2026-09-06-draw", LONG, "premap_alias", "nor->els")
        # and without the pair key on the rows nothing meets
        assert _explain(_board(OTHERWISE, pre_r1=True), S_KBK, T_KBK, EV_TRO,
                        "Yes")["step"] == "no_key_intersection"

    def test_4_two_codes_carrying_the_pair_refuse_league_ambiguous(self, armed):
        twin = dict(OTHERWISE)
        twin["aec-xx-kbk-tro-2026-09-06"] = ("Kristiansund vs. Tromsø", [
            ("atc-xx-kbk-tro-2026-09-06-kbk",
             "Will Kristiansund BK win against Tromso IL in the Eliteserien match scheduled for "
             "Sep 6, 2026?")])
        rows = _board(twin)
        assert _resolve(rows, S_KBK, T_KBK, EV_TRO, "Yes") is None
        ex = _explain(rows, S_KBK, T_KBK, EV_TRO, "Yes")
        assert ex["split"] == "yn:league-ambiguous" and ex["yn_c2"]["league_codes"] == ["els", "xx"]

    def test_5_a_tsc_row_fetched_by_the_pair_key_takes_c3s_own_alias(self, armed):
        venue = dict(OTHERWISE)
        venue[ELS] = ("Kristiansund vs. Tromsø", VENUE[ELS][1] + [
            ("tsc-els-kbk-tro-2026-09-06-fh-1pt5", "Will the total in KBK vs TRO be more than 1.5?"),
            ("tsc-els-kbk-tro-2026-09-06-2pt5", "Will the total in KBK vs TRO be more than 2.5?")])
        rows: list[dict] = []
        for ev_slug, (ev_title, mkts) in venue.items():
            keys = premap.event_keys_for(ev_title, ev_slug)
            for ident, q in mkts:
                m = _market(ident, q)
                if ident.startswith("tsc-"):
                    m["marketSides"] = [{"identifier": ident, "description": "Over", "long": True},
                                        {"identifier": ident, "description": "Under", "long": False}]
                for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m):
                    r["event_keys"] = premap.keys_for_row(keys, r)
                    rows.append(r)
        h = _resolve(rows, "nor-kbk-tro-2026-09-06-first-half-total-1pt5",
                     "Kristiansund BK vs. Tromsø IL: 1st Half O/U 1.5", EV_TRO, "Under")
        assert _short(h) == ("tsc-els-kbk-tro-2026-09-06-fh-1pt5", SHORT, "premap_fh_total", "nor->els")
        # the full-game total: the wording arm's own row, as before C3
        h = _resolve(rows, "nor-kbk-tro-2026-09-06-total-2pt5",
                     "Kristiansund BK vs. Tromsø IL: O/U 2.5", EV_TRO, "Under")
        assert h["market_slug"] == "tsc-els-kbk-tro-2026-09-06-2pt5" and h["intent"] == SHORT

    def test_the_pair_key_carries_his_date_never_another_days(self, armed):
        """nor-sar-vif on his 2026-09-07 against the venue's 2026-09-06
        listing (gap_league_codes R1 pin 4)."""
        venue = {"aec-els-sar-vif-2026-09-06": ("Sarpsborg 08 vs. Valerenga", [
            ("atc-els-sar-vif-2026-09-06-vif",
             "Will Valerenga Fotball win against Sarpsborg 08 FF in the Eliteserien match "
             "scheduled for Sep 6, 2026?")])}
        rows = _board(venue)
        ex = _explain(rows, "nor-sar-vif-2026-09-07-vif", "Will Vålerenga Fotball win on 2026-09-07?",
                      "Sarpsborg 08 FF vs. Vålerenga Fotball", "Yes")
        assert ex["step"] == "no_key_intersection" and ex["rows"] == 0

    def test_the_hot_path_never_reads_the_probe(self):
        import inspect
        assert "league_alias_probe" not in inspect.getsource(premap.resolve)


# ------------------------------------------------- the census (R3)

class TestTheCensusNamesAnUnlistedLeague:
    def test_no_atc_row_on_his_date_carries_either_code_or_name(self, armed):
        """gre1 / bel1 / ere / bra3 on 2026-09-06: the venue lists no
        row naming either club or carrying either code -- the row
        leaves the mapper's bucket by name."""
        rows = _board()
        ex = _explain(rows, "gre1-pan-paok-2026-09-06-pan", "Will Panathinaikos FC win on 2026-09-06?",
                      "Panathinaikos FC vs. PAOK FC", "Yes")
        assert (ex["step"], ex["split"]) == ("no_key_intersection", "venue:league-unlisted")
        assert ex["league_alias_probe"]["would_have_hit"] is False
        assert ex["league_alias_probe"]["name_keys"] == ["panathinaikos fc@2026-09-06",
                                                         "paok fc@2026-09-06"]

    def test_a_listed_row_carrying_his_code_in_position_is_would_have_hit(self, armed):
        """The venue lists his opponent's code at its position under
        other codes and other names: listed, keys unmet -- counted by the
        census as before, never named unlisted."""
        venue = {"aec-lpx-xxx-paok-2026-09-06": ("Xxx vs. Paok", [
            ("atc-lpx-xxx-paok-2026-09-06-paok",
             "Will PAOK Thessaloniki win against Xxx FC in the Super League match scheduled for "
             "Sep 6, 2026?")])}
        rows = _board(venue)
        ex = _explain(rows, "gre1-pan-paok-2026-09-06-pan", "Will Panathinaikos FC win on 2026-09-06?",
                      "Panathinaikos FC vs. PAOK FC", "Yes")
        assert ex["step"] == "no_key_intersection" and "split" not in ex
        assert ex["league_alias_probe"]["would_have_hit"] is True
        assert set(ex["league_alias_probe"]["sample"]) == {"atc-lpx-xxx-paok-2026-09-06-paok"}

    def test_a_slug_outside_the_grammar_asks_nothing(self, armed):
        ex = _explain(_board(), "atp-nakashi-michels-2026-09-06",
                      "US Open ATP: Brandon Nakashima vs Alex Michelsen", None, "Brandon Nakashima")
        assert ex["step"] == "no_key_intersection" and "split" not in ex
        assert "league_alias_probe" not in ex
