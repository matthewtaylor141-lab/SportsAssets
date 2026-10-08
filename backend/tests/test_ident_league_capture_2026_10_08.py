"""VENUE_NATIVE_LEAGUE_NOT_ADMITTED: THE LEAGUES THE VENUE LISTED LATER.

PRODUCTION (first-loss census, read-only readback 2026-10-08 01:40Z, release
08828d04): 5 PinnAPI-native basketball seeds in 24 h refused at MAPPED by
VENUE_NATIVE_LEAGUE_NOT_ADMITTED -- Lahti Basketball v Kataja Basket Joensuu,
Antibes v Orleans Loiret Basket, Manchester Basketball v Cheshire Phoenix,
... The venue listed the fixture and its winner contract; the admitted league
list was a snapshot of the venue's board at 2026-10-06T03:16Z
(pmus_basketball_hockey_winner_listings_2026_10_06.json), and the venue has
listed fourteen more basketball / hockey winner leagues since.

THE SAME EVIDENCE, NOTHING WEAKER. The venue's open listings were read again
(public GET, 2026-10-08T02:22Z, tests/fixtures/
pmus_basketball_hockey_winner_listings_2026_10_08.json, every whole response
hashed). Twelve leagues state a wording BYTE-IDENTICAL to one the first
capture admitted -- overtime included (hockey: overtime and the shootout), a
game not rescheduled within two weeks / two calendar days settled at the last
fair market price -- and are admitted exactly as those were; every listing is
put through the same grading-period match and the same strict comparison.
The two BSKT Cup boards state a DIFFERENT wording ($0.50 on a walkover or a
postponement beyond two days) and are named in LEAGUES_NOT_READ: still
refused by name, never read on a resemblance.
"""
from __future__ import annotations

import json
from pathlib import Path

from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets.agents import paper_benchmark as PB

FIX = Path(__file__).parent / "fixtures"
FIRST = json.loads((FIX / "pmus_basketball_hockey_winner_listings_2026_10_06"
                    ".json").read_text())
LATER = json.loads((FIX / "pmus_basketball_hockey_winner_listings_2026_10_08"
                    ".json").read_text())
NEW_BASKETBALL = {"autbl", "cznbl", "fra2", "gbl", "hunbl", "ita2", "koris",
                  "lkl", "lnb", "slb"}
NEW_HOCKEY = {"del", "shl"}
BSKT_CUP = {"bsktcin", "bsktcua"}


def _fam(sports_type):
    return "hockey" if sports_type.startswith("hockey") else "basketball"


def _wordings(capture):
    out: dict = {}
    for key, ws in capture["wording_census"].items():
        lg, st = key.split("/")
        out[(lg, st)] = {w["after_the_first_sentence"] for w in ws}
    return out


def test_the_capture_is_attributable_and_complete():
    reads = LATER["_reads"]
    assert set(reads) == {"C0", "C500", "C1000", "C1500", "C2000"}
    for r in reads.values():
        assert len(r["response_sha256"]) == 64 and r["response_bytes"] > 0
    # the last page carried no markets: the listing was read to its end
    assert reads["C2000"]["response_bytes"] == len('{"markets":[]}')
    assert "gateway.polymarket.us/v1/markets" in LATER["_source"]
    # the census's three production leagues are in it
    leagues = {k.split("/")[0] for k in LATER["wording_census"]}
    assert {"koris", "fra2", "slb"} <= leagues
    assert leagues == NEW_BASKETBALL | NEW_HOCKEY | BSKT_CUP


def test_each_admitted_league_states_a_wording_the_first_capture_admitted():
    admitted_wordings: dict = {}
    for (lg, st), ws in _wordings(FIRST).items():
        admitted_wordings.setdefault(st, set()).update(ws)
    for (lg, st), ws in _wordings(LATER).items():
        same = ws <= admitted_wordings[st]
        if lg in BSKT_CUP:
            assert not same, lg          # a different wording: not read
            assert lg in V.LEAGUES_NOT_READ
            assert lg not in V.ADMITTED_WINNER_LEAGUES[_fam(st)]
            continue
        assert same, (lg, ws)
        assert lg in V.ADMITTED_WINNER_LEAGUES[_fam(st)], lg


def test_the_identity_and_the_de_vig_admit_the_same_leagues():
    assert NEW_BASKETBALL <= V.ADMITTED_WINNER_LEAGUES["basketball"]
    assert NEW_HOCKEY <= V.ADMITTED_WINNER_LEAGUES["hockey"]
    for fam, leagues in V.ADMITTED_WINNER_LEAGUES.items():
        dv = {k[2] for k in devig.SUPPORTED_BY_LEAGUE if k[:2] == (fam, "h2h")}
        assert dv == set(leagues), fam
    for lg in NEW_BASKETBALL:
        assert devig.expected_outcomes("basketball", "h2h", league=lg) == 2
    for lg in NEW_HOCKEY:
        assert devig.expected_outcomes("hockey", "h2h", league=lg) == 2
    for lg in BSKT_CUP:
        assert devig.expected_outcomes("basketball", "h2h", league=lg) is None


def test_the_not_read_listings_are_kept_apart_from_the_read_ones():
    # every reader of a pmus_*listing* fixture treats `markets` as listings
    # the system reads; the BSKT Cup boards are not read, so theirs are kept
    # apart -- their wording stays verbatim in the census
    assert {m["slug"].split("-")[1] for m in LATER["markets"]} == \
        NEW_BASKETBALL | NEW_HOCKEY
    assert {m["slug"].split("-")[1] for m in LATER["listings_not_read"]} == \
        BSKT_CUP
    for m in LATER["listings_not_read"]:
        assert "$0.50" in m["description"], m["slug"]


def test_every_admitted_listing_grades_like_the_book_and_strict_terms_conflict():
    seen = set()
    for m in LATER["markets"] + LATER["listings_not_read"]:
        lg = m["slug"].split("-")[1]
        fam = _fam(m["sportsMarketType"])
        book = PB.book_grading_period(fam, lg)
        if lg in BSKT_CUP:
            assert book is None, lg      # no grading claimed for it
            continue
        seen.add(lg)
        venue = PB.venue_grading_period(fam, m["description"], lg)
        # (the winner sentence's own "St." -- "SKN St. Polten Basketball",
        # aec-autbl-pol-tra -- is read by the template, as in the first)
        assert venue["refusal"] is None, (m["slug"], venue)
        assert venue["period"] == book["period"], m["slug"]
        a = vset.attest(sport_family=fam, market="h2h",
                        venue_evidence={"rules_text": m["description"]},
                        book_evidence={"outcome_names": ["H", "A"]},
                        observed_at=1.0)
        assert a["rules"]["overtime"]["established"] is True, m["slug"]
        assert a["unmet"] == [vset.R_VOID_CONFLICTS], (m["slug"], a["unmet"])
        assert a["rules"]["void"]["mismatched_conditions"] == [
            T.C_NOT_PLAYED]
    assert seen == NEW_BASKETBALL | NEW_HOCKEY


def test_a_league_no_capture_names_is_still_refused_by_name():
    for fam in ("basketball", "hockey"):
        assert "zzz" not in V.ADMITTED_WINNER_LEAGUES[fam]
        assert PB.book_grading_period(fam, "zzz") is None
    assert V.R_LEAGUE_NOT_ADMITTED == "VENUE_NATIVE_LEAGUE_NOT_ADMITTED"
