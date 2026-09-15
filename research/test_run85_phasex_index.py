#!/usr/bin/env python3
"""Offline gate for the Phase X EXECUTION repair: the indexed pairing must
return exactly what the frozen all-by-all pairing returned.

CONTACTS NOTHING.

WHY THIS FILE EXISTS. Run 34971707012 reached the live board and then died
on the workflow's 60-minute timeout inside the PMUS x Kalshi comparison.
The repair hoists deterministic per-contract parsing and skips pairs an
index can PROVE are EQUIVALENCE_REJECTED. An optimisation to a scientific
gate is only acceptable if it is provably result-preserving, so the
reference implementations below are copied VERBATIM from the frozen
driver at commit f8d095c and every test compares against them rather than
against the new code's own idea of itself.

Run:  python3 -m pytest research/test_run85_phasex_index.py -q
      python3 research/test_run85_phasex_index.py
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path


def _load(name, filename):
    s = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


K = _load("px_kalshi", "run85_phasex_kalshi.py")
E = _load("px_econ", "run85_phasex_economics.py")
D = _load("px_run", "run85_phasex_run.py")

VERIFIED, QUOTED, ABSENT = K.VERIFIED, K.QUOTED, K.ABSENT


# ================================================ FROZEN REFERENCE CODE ==
# Copied verbatim from research/run85_phasex_run.py at f8d095c -- the
# revision that ran live as 34971707012. Do not "tidy" these: their whole
# value is being the thing the repair is measured against.

def dimensions_reference(prec: dict, krec: dict) -> dict:
    def pv(key, sub=None):
        n = prec.get(key) or {}
        v = n.get("value")
        return (v or {}).get(sub) if (sub and isinstance(v, dict)) else v

    def kv(key):
        return (krec.get(key) or {}).get("value")

    def quoted(rec, slot):
        n = rec.get(slot) or {}
        if n.get("status") != K.QUOTED:
            return None
        return " ".join(h["text"] for h in n["value"])

    return {
        "SPORT": (pv("SPORT"), None),
        "LEAGUE": (pv("LEAGUE"), kv("SERIES_TICKER")),
        "UNDERLYING_EVENT": (pv("EVENT", "title"), kv("EVENT_TITLE")),
        "PARTICIPANTS": (pv("PARTICIPANTS_FROM_SIDES"),
                         [x for x in (kv("YES_SUB_TITLE"),
                                      kv("NO_SUB_TITLE")) if x] or None),
        "PROPOSITION": (None, None),
        "LINE": (pv("MARKET_TYPE", "line"), kv("STRIKE")),
        "PERIOD": (pv("MARKET_TYPE", "sportsMarketType"), kv("MARKET_TYPE")),
        "START_TIME": (pv("GAME_START_TIME"), None),
        "CLOSE_TIME": (pv("END_TIME"), kv("CLOSE_TIME")),
        "SETTLEMENT_SOURCE": (pv("LEAGUE_RESOLUTION_SOURCE"),
                              kv("SETTLEMENT_SOURCES")),
        "SETTLEMENT_RULE": (quoted(prec, "OTHER_RESOLUTION_CONDITIONS"),
                            quoted(krec, "OTHER_RESOLUTION_CONDITIONS")),
        "OVERTIME": (quoted(prec, "OVERTIME_RULES"),
                     quoted(krec, "OVERTIME_RULES")),
        "EXTRA_TIME": (None, None),
        "POSTPONEMENT": (quoted(prec, "POSTPONEMENT_RULES"),
                         quoted(krec, "POSTPONEMENT_RULES")),
        "CANCELLATION": (quoted(prec, "CANCELLATION_RULES"),
                         quoted(krec, "CANCELLATION_RULES")),
        "DRAW_TIE": (quoted(prec, "DRAW_RULES"), quoted(krec, "DRAW_RULES")),
        "VOID": (quoted(prec, "VOID_RULES"), quoted(krec, "VOID_RULES")),
        "OTHER_MATERIAL_CONDITIONS": (None, None),
    }


def classify_pairs_reference(precs: list, krecords: list) -> tuple:
    """The frozen all-by-all loop, with its own retention filter and its
    own three tallies. Returns (verified, counts) in the repair's shape so
    the two can be compared field by field."""
    pairs = []
    for prec in precs:
        pp = D.pmus_side(prec)
        for krec in krecords:
            kp = D.kalshi_side(krec)
            gate = E.equivalence(pp, kp, dimensions_reference(prec, krec))
            if gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_NOT_IDENTIFIED \
                    and not gate["DIMENSION_RESULTS"].get("PARTICIPANTS") \
                    == E.MATCH:
                continue              # not even a candidate pairing
            pairs.append({"PMUS": prec, "KALSHI": krec, "GATE": gate})

    verified = [p for p in pairs
                if p["GATE"]["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_VERIFIED]
    rejected = [p for p in pairs
                if p["GATE"]["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_REJECTED]
    unknown = [p for p in pairs
               if p["GATE"]["EQUIVALENCE_STATUS"]
               == E.EQUIVALENCE_NOT_IDENTIFIED]
    return verified, {
        "PMUS_CONTRACTS_DISCOVERED": len(precs),
        "KALSHI_CONTRACTS_DISCOVERED": len(krecords),
        "PAIRS_ALL_BY_ALL": len(precs) * len(krecords),
        "POTENTIAL_MATCHES": len(pairs),
        "EQUIVALENCE_VERIFIED": len(verified),
        "EQUIVALENCE_REJECTED": len(rejected),
        "EQUIVALENCE_NOT_IDENTIFIED": len(unknown),
    }


# ============================================================ FIXTURES ==
# PMUS records in PX1-PMUS-1 node shape; Kalshi records built by the real
# ingest function from the wire shape this repo's own trading client
# already parses.

def node(value, status=VERIFIED, source="fixture"):
    return {"value": value, "status": status, "source": source}


TENNIS_PROSE = (
    "This market will settle to the winner of the J.J. Wolf vs Luciano "
    "Emanuel Ambrogi ATP match scheduled for September 14. If the match "
    "does not begin, the market will settle to fair market price.")

NFL_PROSE = (
    "This market will settle to the winner of the Denver Broncos vs "
    "Kansas City Chiefs NFL game. Overtime is included if played.")

SUBJECT_PROSE = (
    "The market will settle to Yes if Kansas City Chiefs outscores "
    "Denver Broncos by more than 21.5 points.")


def pmus_record(slug, event_title, side1, side2, prose,
                league="ATP", period="tennis_match_winner", line=None,
                close="2026-09-28T20:00:00Z"):
    return {
        "PMUS_MARKET_SLUG": node(slug, source="market.slug"),
        "EVENT": node({"title": event_title, "slug": slug},
                      source="event.title"),
        "SPORT": node(None, ABSENT, "venue serves no sport-name field"),
        "LEAGUE": node(league, source="event.tags[].league.name"),
        "PARTICIPANTS_FROM_SIDES": node([side1, side2],
                                        source="marketSides[].team.name"),
        "MARKET_TYPE": node({"line": line, "marketType": "moneyline",
                             "sportsMarketType": period},
                            source="market.sportsMarketTypeV2"),
        "SIDE_1_DEFINITION": {
            "SIDE_TEAM_NAME": node(side1, source="marketSides[].team.name"),
            "SIDE_DESCRIPTION": node(side1,
                                     source="marketSides[].description")},
        "SIDE_2_DEFINITION": {
            "SIDE_TEAM_NAME": node(side2, source="marketSides[].team.name"),
            "SIDE_DESCRIPTION": node(side2,
                                     source="marketSides[].description")},
        "LEAGUE_RESOLUTION_SOURCE": node(None, ABSENT, "no field"),
        "END_TIME": node(close, source="market.endDate"),
        "GAME_START_TIME": node("2026-09-14T20:00:00Z",
                                source="market.gameStartTime"),
        "SOURCE_TEXTS": {"MARKET_DESCRIPTION": prose},
        "OTHER_RESOLUTION_CONDITIONS": node(None, ABSENT, "none"),
        "OVERTIME_RULES": node(None, ABSENT, "none"),
        "POSTPONEMENT_RULES": node(None, ABSENT, "none"),
        "CANCELLATION_RULES": node(None, ABSENT, "none"),
        "DRAW_RULES": node(None, ABSENT, "none"),
        "VOID_RULES": node(None, ABSENT, "none"),
    }


def kalshi_record(ticker, event_title, yes, no, rules,
                  series="KXATPMATCH", close="2026-09-28T20:00:00Z"):
    market = {
        "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
        "series_ticker": series, "market_type": "binary",
        "title": "%s?" % event_title, "yes_sub_title": yes,
        "no_sub_title": no, "status": "active", "close_time": close,
        "rules_primary": rules, "rules_secondary": "",
        "settlement_sources": [{"name": series}],
    }
    event = {"event_ticker": market["event_ticker"], "series_ticker": series,
             "title": event_title, "category": "Sports"}
    return K.kalshi_contract_record(market, event)


def fixture():
    """A board carrying every case the owner named."""
    tennis_event = "J.J. Wolf vs. Luciano Emanuel Ambrogi"
    nfl_event = "Denver Broncos at Kansas City Chiefs"

    precs = [
        # 0 -- a clean contest-style settlement sentence
        pmus_record("aec-atp-wolf-ambrogi", tennis_event,
                    "J.J. Wolf", "Luciano Emanuel Ambrogi", TENNIS_PROSE),
        # 1 -- same event, the other side (participant-order reversal)
        pmus_record("aec-atp-ambrogi-wolf", tennis_event,
                    "Luciano Emanuel Ambrogi", "J.J. Wolf", TENNIS_PROSE),
        # 2 -- a different event entirely
        pmus_record("aec-nfl-den-kc", nfl_event, "Kansas City Chiefs",
                    "Denver Broncos", NFL_PROSE, league="NFL",
                    period="moneyline"),
        # 3 -- AMBIGUOUS/CONTRADICTED subject: the prose names a side
        #      outside the matchup while the venue's side field is the
        #      other one
        pmus_record("aec-nfl-den-kc-spread", nfl_event,
                    "Denver Broncos", "Kansas City Chiefs", SUBJECT_PROSE,
                    league="NFL", period="spread", line=21.5),
        # 4 -- NO EVENT TITLE. Nothing about it is ever provable by the
        #      index, so every pair it is in must be evaluated in full.
        {**pmus_record("aec-atp-no-event", tennis_event, "J.J. Wolf",
                       "Luciano Emanuel Ambrogi", TENNIS_PROSE),
         "EVENT": node({"slug": "x"}, source="event.title")},
        # 5 -- ALIGNED so that NO dimension mismatches. Reaches
        #      EQUIVALENCE_NOT_IDENTIFIED with PARTICIPANTS == MATCH, which
        #      is the one NOT_IDENTIFIED case the frozen filter RETAINS and
        #      counts. Without it the conformance counts would agree for
        #      the wrong reason -- every pair rejected.
        pmus_record("aec-atp-aligned", tennis_event, "J.J. Wolf",
                    "Luciano Emanuel Ambrogi", TENNIS_PROSE,
                    league="KXATPMATCH", period="binary"),
        # 6 -- same, but the venue published no participant list, so
        #      PARTICIPANTS is UNRESOLVED and the frozen filter DROPS the
        #      pair uncounted. The other NOT_IDENTIFIED branch.
        {**pmus_record("aec-atp-aligned-nopart", tennis_event, "J.J. Wolf",
                       "Luciano Emanuel Ambrogi", TENNIS_PROSE,
                       league="KXATPMATCH", period="binary"),
         "PARTICIPANTS_FROM_SIDES": node(None, ABSENT, "absent")},
    ]

    krecords = [
        # 0 -- the tennis match, same event string
        kalshi_record("KXATPMATCH-WOLFAMB-WOLF", tennis_event,
                      "J.J. Wolf", "Luciano Emanuel Ambrogi", TENNIS_PROSE),
        # 1 -- same event, sides reversed
        kalshi_record("KXATPMATCH-WOLFAMB-AMB", tennis_event,
                      "Luciano Emanuel Ambrogi", "J.J. Wolf", TENNIS_PROSE),
        # 2 -- the NFL game
        kalshi_record("KXNFLGAME-DENKC-KC", nfl_event, "Kansas City Chiefs",
                      "Denver Broncos", NFL_PROSE, series="KXNFLGAME"),
        # 3 -- SETTLEMENT/RULE CONFLICT: same event, incompatible prose
        kalshi_record("KXNFLGAME-DENKC-ALT", nfl_event,
                      "Kansas City Chiefs", "Denver Broncos",
                      NFL_PROSE + " If the game is cancelled the market "
                      "resolves to No and overtime is excluded.",
                      series="KXNFLGAME"),
        # 4 -- an unrelated event
        kalshi_record("KXEPLGAME-ARSCHE-ARS", "Arsenal vs Chelsea",
                      "Arsenal", "Chelsea",
                      "If Arsenal win the match, the market resolves to "
                      "Yes.", series="KXEPLGAME"),
        # 5 -- NO EVENT TITLE on the Kalshi side
        {**kalshi_record("KXATPMATCH-NOEVENT", tennis_event, "J.J. Wolf",
                         "Luciano Emanuel Ambrogi", TENNIS_PROSE),
         "EVENT_TITLE": node(None, ABSENT, "absent")},
    ]
    return precs, krecords


def ids(pairs):
    return sorted(((p["PMUS"]["PMUS_MARKET_SLUG"] or {}).get("value"),
                   (p["KALSHI"]["TICKER"] or {}).get("value"))
                  for p in pairs)


# =============================================================== TESTS ==
def test_the_fixture_actually_contains_every_named_case():
    """A conformance test whose fixture is degenerate proves nothing."""
    precs, krecords = fixture()
    assert len(precs) == 7 and len(krecords) == 6
    # all three outcomes must be present, or the conformance tests could
    # agree for the wrong reason.
    ref_v, ref_c = classify_pairs_reference(precs, krecords)
    assert ref_c["EQUIVALENCE_REJECTED"] > 0
    assert ref_c["EQUIVALENCE_NOT_IDENTIFIED"] > 0
    assert ref_c["POTENTIAL_MATCHES"] < ref_c["PAIRS_ALL_BY_ALL"], \
        "a dropped NOT_IDENTIFIED pair must be present"
    ph = [D.pmus_dimension_half(p) for p in precs]
    kh = [D.kalshi_dimension_half(k) for k in krecords]
    keys_p = {D.event_key(h["UNDERLYING_EVENT"]) for h in ph}
    keys_k = {D.event_key(h["UNDERLYING_EVENT"]) for h in kh}
    assert None in keys_p and None in keys_k        # unkeyed on both sides
    assert len(keys_p - {None}) >= 2                # more than one event
    assert len(keys_k - {None}) >= 3
    # a shared event exists, so the index must NOT eliminate everything
    assert (keys_p & keys_k) - {None}
    # participant-order reversals are present on both venues
    assert ph[0]["PARTICIPANTS"] == list(reversed(ph[1]["PARTICIPANTS"]))
    assert kh[0]["PARTICIPANTS"] == list(reversed(kh[1]["PARTICIPANTS"]))
    # a settlement/rule conflict is present
    assert kh[2]["OVERTIME"] != kh[3]["OVERTIME"]
    # an ambiguous / contradicted payoff subject is present
    assert D.pmus_side(precs[3])["PAYOFF_STATUS"] != E.PAYOFF_VERIFIED


def test_the_halved_dimensions_are_byte_identical_to_the_frozen_pairwise():
    """Hoisting must be a rearrangement, not a reimplementation."""
    precs, krecords = fixture()
    for p in precs:
        for k in krecords:
            assert D.dimensions(p, k) == dimensions_reference(p, k)


def test_per_contract_parsing_is_deterministic_so_hoisting_is_sound():
    """Caching one call per contract is only valid if the call depends on
    that contract and nothing else."""
    precs, krecords = fixture()
    for p in precs:
        assert D.pmus_side(p) == D.pmus_side(copy.deepcopy(p))
        assert D.pmus_dimension_half(p) == D.pmus_dimension_half(p)
    for k in krecords:
        assert D.kalshi_side(k) == D.kalshi_side(copy.deepcopy(k))
        assert D.kalshi_dimension_half(k) == D.kalshi_dimension_half(k)


def test_indexed_equals_exhaustive_on_every_count():
    precs, krecords = fixture()
    ref_v, ref_c = classify_pairs_reference(precs, krecords)
    got_v, got_c, _census = D.classify_pairs(precs, krecords)
    for key in ("PMUS_CONTRACTS_DISCOVERED", "KALSHI_CONTRACTS_DISCOVERED",
                "PAIRS_ALL_BY_ALL", "POTENTIAL_MATCHES",
                "EQUIVALENCE_VERIFIED", "EQUIVALENCE_REJECTED",
                "EQUIVALENCE_NOT_IDENTIFIED"):
        assert got_c[key] == ref_c[key], (key, got_c[key], ref_c[key])


def test_indexed_equals_exhaustive_on_the_identity_of_every_verified_pair():
    """Counts can agree while the wrong pairs pass. They must be the same
    pairs, and carry the same gate."""
    precs, krecords = fixture()
    ref_v, _ = classify_pairs_reference(precs, krecords)
    got_v, _, _ = D.classify_pairs(precs, krecords)
    assert ids(got_v) == ids(ref_v)
    by_ref = {i: p for i, p in zip(ids(ref_v), sorted(
        ref_v, key=lambda p: ((p["PMUS"]["PMUS_MARKET_SLUG"] or {})
                              .get("value"),
                              (p["KALSHI"]["TICKER"] or {}).get("value"))))}
    by_got = {i: p for i, p in zip(ids(got_v), sorted(
        got_v, key=lambda p: ((p["PMUS"]["PMUS_MARKET_SLUG"] or {})
                              .get("value"),
                              (p["KALSHI"]["TICKER"] or {}).get("value"))))}
    for i in by_ref:
        assert by_got[i]["GATE"] == by_ref[i]["GATE"], i


def test_every_pair_the_index_skips_really_is_rejected_by_the_full_gate():
    """THE SAFETY PROPERTY. The index claims a skipped pair is
    EQUIVALENCE_REJECTED. Run the gate on every one of them and check."""
    precs, krecords = fixture()
    skipped = 0
    for p in precs:
        ph = D.pmus_dimension_half(p)
        pp = D.pmus_side(p)
        for k in krecords:
            kh = D.kalshi_dimension_half(k)
            if not D.index_eliminates(ph["UNDERLYING_EVENT"],
                                      kh["UNDERLYING_EVENT"]):
                continue
            skipped += 1
            gate = E.equivalence(pp, D.kalshi_side(k),
                                 D.dimensions_from_halves(ph, kh))
            assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_REJECTED
    assert skipped > 0, "the fixture must exercise the elimination path"


def test_the_index_never_promotes_a_pair():
    """Elimination yields REJECTED and only REJECTED. Nothing the index
    does can turn a pair into VERIFIED or move it out of REJECTED."""
    precs, krecords = fixture()
    _, ref_c = classify_pairs_reference(precs, krecords)
    _, got_c, _ = D.classify_pairs(precs, krecords)
    assert got_c["EQUIVALENCE_VERIFIED"] <= ref_c["EQUIVALENCE_VERIFIED"]
    assert got_c["EQUIVALENCE_VERIFIED"] == ref_c["EQUIVALENCE_VERIFIED"]
    # and the index is not silently inert -- it did eliminate work
    assert got_c["EQUIVALENCE_EVALUATIONS"] < got_c["PAIRS_ALL_BY_ALL"]
    assert got_c["PAIRS_ELIMINATED_BY_INDEX"] > 0
    assert (got_c["EQUIVALENCE_EVALUATIONS"]
            + got_c["PAIRS_ELIMINATED_BY_INDEX"]) == got_c["PAIRS_ALL_BY_ALL"]


def test_a_contract_with_no_event_title_is_never_eliminated():
    """Two silences are not a mismatch. A contract either venue did not
    give an event title for has no elimination proof against anything, and
    must be compared against the whole other side."""
    precs, krecords = fixture()
    ph = [D.pmus_dimension_half(p) for p in precs]
    kh = [D.kalshi_dimension_half(k) for k in krecords]
    p_blank = [i for i, h in enumerate(ph)
               if D.event_key(h["UNDERLYING_EVENT"]) is None]
    k_blank = [j for j, h in enumerate(kh)
               if D.event_key(h["UNDERLYING_EVENT"]) is None]
    assert p_blank and k_blank
    for i in p_blank:
        for j in range(len(krecords)):
            assert not D.index_eliminates(ph[i]["UNDERLYING_EVENT"],
                                          kh[j]["UNDERLYING_EVENT"])
    for j in k_blank:
        for i in range(len(precs)):
            assert not D.index_eliminates(ph[i]["UNDERLYING_EVENT"],
                                          kh[j]["UNDERLYING_EVENT"])


def test_the_index_key_is_exactly_what_compare_dimension_compares():
    """event_key() predicts compare_dimension(). If it ever disagreed, an
    eliminated pair could have been a MATCH."""
    samples = ["Arsenal vs Chelsea", " Arsenal vs Chelsea ",
               "ARSENAL VS CHELSEA", "Denver at KC", "", "   ", None,
               123, {"title": "x"}, ["a"]]
    for a in samples:
        for b in samples:
            ka, kb = D.event_key(a), D.event_key(b)
            if ka is not None and kb is not None:
                expect = E.MATCH if ka == kb else E.MISMATCH
                assert E.compare_dimension(a, b) == expect, (a, b)
                assert D.index_eliminates(a, b) == (expect == E.MISMATCH)
            else:
                assert not D.index_eliminates(a, b), (a, b)


def test_the_equivalence_gate_itself_is_untouched_by_the_repair():
    """The repair may not edit the authority. Pin the gate's vocabulary,
    its dimension list and its three-way rule."""
    assert E.MATERIAL_DIMENSIONS == (
        "SPORT", "LEAGUE", "UNDERLYING_EVENT", "PARTICIPANTS", "PROPOSITION",
        "LINE", "PERIOD", "START_TIME", "CLOSE_TIME", "SETTLEMENT_SOURCE",
        "SETTLEMENT_RULE", "OVERTIME", "EXTRA_TIME", "POSTPONEMENT",
        "CANCELLATION", "DRAW_TIE", "VOID", "OTHER_MATERIAL_CONDITIONS")
    src = Path(E.__file__).read_text()
    assert "def equivalence(" in src and "def compare_dimension(" in src
    # the driver must not define its own copy of either
    dsrc = Path(D.__file__).read_text()
    assert "def equivalence(" not in dsrc
    assert "def compare_dimension(" not in dsrc


def test_the_dimension_census_accounts_for_every_evaluation():
    """The census is instrumentation, so it must add up rather than
    approximate: each dimension is scored exactly once per evaluation."""
    precs, krecords = fixture()
    _, counts, census = D.classify_pairs(precs, krecords)
    for dim, c in census["DIMENSION_CENSUS"].items():
        assert sum(c.values()) == counts["EQUIVALENCE_EVALUATIONS"], dim


def test_five_dimensions_can_never_match_so_verified_is_unreachable():
    """A STRUCTURAL FINDING, pinned so it cannot be forgotten or quietly
    changed. dimensions() hard-codes None on at least one venue for SPORT,
    PROPOSITION, START_TIME, EXTRA_TIME and OTHER_MATERIAL_CONDITIONS, so
    compare_dimension() scores them UNRESOLVED on EVERY pair and
    equivalence() can never return EQUIVALENCE_VERIFIED. GATE D is the
    only reachable outcome of the frozen design.

    This test does NOT endorse that. It records it, so that any future
    change to the criteria is a deliberate act with a failing test
    attached -- and so nobody reads a GATE D result as evidence that the
    two venues carry no equivalent contracts.
    """
    precs, krecords = fixture()
    structural = ("SPORT", "PROPOSITION", "START_TIME", "EXTRA_TIME",
                  "OTHER_MATERIAL_CONDITIONS")
    for p in precs:
        for k in krecords:
            dims = D.dimensions(p, k)
            for d in structural:
                assert E.compare_dimension(*dims[d]) == E.UNRESOLVED, (d, p)
    _, counts, _ = D.classify_pairs(precs, krecords)
    assert counts["EQUIVALENCE_VERIFIED"] == 0
    ref_v, _ = classify_pairs_reference(precs, krecords)
    assert ref_v == []          # the frozen path agrees: it is structural


def test_the_repair_touches_no_rate_or_venue_policy():
    dsrc = Path(D.__file__).read_text()
    for forbidden in ("SPACING_S =", "MAX_VENUE_REQUESTS =", "RPS",
                      "time.sleep("):
        assert forbidden not in dsrc, forbidden
    assert K.KALSHI_RATE_LIMIT_NOT_ESTABLISHED is True


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
