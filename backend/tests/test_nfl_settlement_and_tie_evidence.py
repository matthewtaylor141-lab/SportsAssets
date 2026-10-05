"""R30A · THE NFL INTEGRATION, PIECE BY PIECE (pure; no database).

  1 THE CITATIONS ARE THE PUBLISHERS' OWN WORDS: every quote the module pays
    on is found verbatim in a captured document (Pinnacle's rules page, the
    venue's listings, the tie-rate source, PinnAPI's docs).
  2 THE TIE RATE: the exact Clopper-Pearson interval over the cited count,
    its denominator bracketed, and the worst end chosen PER SIDE.
  3 THE CONVERSION refuses by name whenever a premise is missing, and does
    nothing outside the NFL (no other sport's number moves).
  4 SETTLEMENT, compared as PAYOUTS: the tie (book void vs venue 0.50) and
    the postponement (book void vs last fair market price) stay named
    mismatches for the strict policy, whose remaining needs are listed.
  5 THE COMPLETED-GAME MATCH: NFL terms established from the venue's own
    text; the college board left exactly where it was.
  6 PINNAPI: football is sport 5, the period-0 two-way line; a draw-priced
    football line is refused by name; the held read's family spelling.
  7 HOT NFL FRESHNESS PRIORITY: held first, hot NFL near kickoff second,
    discovery third; hot seeds survive discovery eviction.
  8 THE PAPER SETTLEMENT PAYS A STATED 0.50, AND NAMES ITS STATE ONLY AS FAR
    AS IT IS KNOWN (a tie and a last-fair-price 0.50 are not told apart).
  9 (R30A review) THE PHASE IS ESTABLISHED FROM THE CITED SEASON WINDOW, the
    tie clause must be EXACTLY the cited sentence, the census admits the
    production NFL catalogue rows (their clock-stamped line proved), the held
    read answers for an NFL row end to end, and the hot tier yields to
    waiting discovery work.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sportsassets import bettor_nfl_settlement as NFL
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_settlement as vset
from sportsassets import pinnapi_census as CEN
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_primary as P
from sportsassets import pinnapi_reactive as R
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_xavier as PX

FIX = Path(__file__).parent / "fixtures"
PIN = json.loads((FIX / "pinnacle_american_football_rules_2026_10_03.json")
                 .read_text())
NFL_LISTING = json.loads((FIX / "pmus_nfl_listing_2026_10_04.json")
                         .read_text())
SNF_LISTING = json.loads((FIX / "pmus_nfl_listing_2026_10_04_snf_mnf.json")
                         .read_text())
CFB_LISTING = json.loads((FIX / "pmus_cfb_listing_2026_10_03.json")
                         .read_text())
TIE = json.loads((FIX / "nfl_tie_rate_evidence_2026_10_04.json").read_text())
SEASON = json.loads((FIX / "nfl_season_window_2026_10_04.json").read_text())
#: production's own NFL catalogue rows (research-sql run 37232171531)
PROD_ROWS = json.loads((FIX / "pmus_nfl_catalogue_rows_2026_10_04.json")
                       .read_text())
PDOC = json.loads((FIX / "pinnapi_docs_2026_10_04.json").read_text())
NFL_TEXT = NFL_LISTING["markets"][0]["description"]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE CITATIONS
# ═════════════════════════════════════════════════════════════════════

def test_every_book_quote_is_verbatim_in_the_captured_rules_page():
    general = PIN["general_rules_section"]
    football = PIN["american_football_section"]
    assert NFL.Q_BOOK_TIE in general                    # General Rule 11
    assert NFL.Q_BOOK_VENUE_CHANGED in general
    assert NFL.Q_BOOK_PRECEDENCE in general
    for q in (NFL.Q_BOOK_OVERTIME, NFL.Q_BOOK_NOT_STARTED,
              NFL.Q_BOOK_SUSPENDED, NFL.Q_BOOK_PRO_BOWL, NFL.Q_BOOK_LEAGUES):
        assert q in football, q
    assert PIN["page_sha256"] == NFL.PINNACLE_PAGE_SHA256
    # the R30A re-read is byte-identical to the captured page
    assert {c["sha256"] for c in NFL.PINNACLE_CAPTURES} == {
        NFL.PINNACLE_PAGE_SHA256}
    assert {c["bytes"] for c in NFL.PINNACLE_CAPTURES} == {PIN["page_bytes"]}


def test_every_venue_quote_is_verbatim_in_every_captured_nfl_listing():
    texts = [m["description"] for m in NFL_LISTING["markets"]
             + SNF_LISTING["markets"]]
    assert len(texts) == 5
    for t in texts:
        for q in (NFL.Q_VENUE_OVERTIME, NFL.Q_VENUE_TIE,
                  NFL.Q_VENUE_POSTPONED):
            assert q in t, (q, t)
    assert NFL_LISTING["_evidence"]["response_sha256"] == \
        NFL.VENUE_CAPTURES[0]["sha256"]
    assert SNF_LISTING["_evidence"]["response_sha256"] == \
        NFL.VENUE_CAPTURES[1]["sha256"]
    # the college listing words its tie differently (not a $0.50 settlement)
    assert NFL.Q_VENUE_TIE not in CFB_LISTING["markets"][0]["description"]


def test_the_tie_rate_evidence_is_the_retrieved_text():
    ev = NFL.TIE_RATE_EVIDENCE
    sec = TIE["section_2025_present"]
    assert ev["rule_quote"] == sec["rule_sentence"]
    assert ev["count_quote"] in sec["count_sentence"].replace(
        "[[2026 NFL season|2026 season]]", "2026 season")
    assert ev["ties"] == len(sec["table_rows"]) == 1
    assert sec["table_rows"][0]["date"] == "September 28, 2025"
    assert TIE["revision"]["revid"] == 1377928469
    assert "1377928469" in ev["source_url"]
    assert ev["retrievals"][0]["sha256"] == sec["read_by"]["sha256"]
    assert ev["retrievals"][1]["sha256"] == TIE["section_lead"]["read_by"][
        "sha256"]
    assert "272-game" in TIE["schedule_size"]["sentence"]
    assert ev["games_min"] == 272 and ev["games_max"] == 272 + 48


def test_pinnapi_sport_ids_are_the_providers_own():
    table = PDOC["sport_ids_table"]
    for fam, sid in P.SPORTS.items():
        # (integration) inc-pinnapi's six-sport map adds basketball, hockey
        # and tennis; each id is still checked against the provider's own
        # documented table
        assert table[str(sid)].lower().startswith(
            {"baseball": "baseball", "soccer": "soccer",
             "football": "football", "basketball": "basketball",
             "hockey": "hockey", "tennis": "tennis"}[fam]), (fam, sid)
    assert P.SPORTS["football"] == 5
    assert "num_0` — full match" in PDOC["periods"][0]
    assert "draw?" in PDOC["market_types_row"]


# ═════════════════════════════════════════════════════════════════════
# 2 · THE TIE RATE AND THE WORST END PER SIDE
# ═════════════════════════════════════════════════════════════════════

def test_clopper_pearson_is_exact():
    lo, hi = NFL.clopper_pearson(1, 272)
    # the defining equations, evaluated independently
    assert NFL.binom_cdf(1, 272, hi) == pytest.approx(0.025, abs=1e-9)
    assert 1.0 - NFL.binom_cdf(0, 272, lo) == pytest.approx(0.025, abs=1e-9)
    assert lo == pytest.approx(1 - 0.975 ** (1 / 272), rel=1e-6)
    assert NFL.clopper_pearson(0, 50)[0] == 0.0
    with pytest.raises(ValueError):
        NFL.clopper_pearson(3, 2)


def test_the_interval_is_bracketed_and_wide_where_it_must_be():
    iv = NFL.tie_rate_interval()
    assert iv["held"]
    assert iv["lo"] == pytest.approx(NFL.clopper_pearson(1, 320)[0])
    assert iv["hi"] == pytest.approx(NFL.clopper_pearson(1, 272)[1])
    assert 0.0 < iv["lo"] < 1 / 320 < 1 / 272 < iv["hi"] < 0.021
    # unusable evidence is a named refusal, never a zero
    bad = NFL.tie_rate_interval({"ties": 1})
    assert bad["held"] is False and bad["refusal"] == NFL.R_TIE_RATE_NOT_HELD


def test_the_worst_end_is_chosen_per_side():
    iv = NFL.tie_rate_interval()
    reg = dict(phase=NFL.PHASE_REGULAR)
    fav = NFL.venue_value(0.70, **reg)
    assert fav["tie_rate_end_used"] == "HIGHEST"
    assert fav["p"] == pytest.approx(0.70 - iv["hi"] * 0.20)
    dog = NFL.venue_value(0.30, **reg)
    assert dog["tie_rate_end_used"] == "LOWEST"
    assert dog["tie_rate_used"] == pytest.approx(iv["lo"])
    assert dog["p"] == pytest.approx(0.30 + iv["lo"] * 0.20)
    # the worst case is never above either end's value
    for p in (0.05, 0.3, 0.5, 0.62, 0.95):
        vv = NFL.venue_value(p, **reg)
        assert vv["p"] <= min(vv["p_venue_at_interval_ends"]) + 1e-15


def test_a_phase_that_is_not_regular_season_is_never_priced():
    """R30A review: the 'union of both cases' (phase None -> lower end 0)
    priced a preseason game on the regular-season tie rate. Every phase but
    an ESTABLISHED regular season now refuses by name."""
    for phase in (None, "", "PRESEASON", "POSTSEASON", "PRO_BOWL"):
        vv = NFL.venue_value(0.30, phase=phase)
        assert vv["p"] is None and vv["refusal"] == NFL.R_PHASE_NOT_REGULAR
        got = NFL.convert(0.62, sport_family="football", league="nfl",
                          venue_rules_text=NFL_TEXT, phase=phase)
        assert got["applies"] is True and got["p"] is None
        assert got["refusal"] == NFL.R_PHASE_NOT_REGULAR


def test_the_season_window_is_the_retrieved_text():
    w = NFL.SEASON_WINDOWS[0]
    assert w["infobox_quote"] in SEASON["wikitext"]
    assert SEASON["read_by"]["sha256"] == w["sha256"]
    assert SEASON["read_by"]["run_id"] == w["run_id"]
    assert SEASON["revid"] == 1378055076 and "1378055076" in w["source_url"]
    assert "{{Start date|2026|09|09|}}" in w["infobox_quote"]
    assert "{{End date|2027|01|10}}" in w["infobox_quote"]
    assert (w["regular_season_first_day"], w["regular_season_last_day"]) == (
        "2026-09-09", "2027-01-10")
    assert "will end on January 10, 2027" in SEASON["wikitext"]


def test_the_phase_is_established_from_the_cited_window():
    ok = {d: NFL.season_phase(d) for d in (
        "2026-09-09", "2026-10-04", "2026-12-25", "2027-01-10")}
    for d, ph in ok.items():
        assert ph["phase"] == NFL.PHASE_REGULAR and ph["refusal"] is None, d
        assert ph["cite"]["run_id"] == 37231698293
    # preseason / Hall of Fame Game (before), playoffs and the Pro Bowl
    # (after), another season, and an unreadable day: all refused by name
    for d in ("2026-08-06", "2026-08-15", "2026-09-08", "2027-01-11",
              "2027-01-17", "2027-02-07", "2027-02-14", "2027-09-12",
              None, "not-a-date"):
        ph = NFL.season_phase(d)
        assert ph["phase"] is None, d
        assert ph["refusal"] == NFL.R_PHASE_NOT_REGULAR, d
    assert "BEFORE" in NFL.season_phase("2026-08-15")["why"]
    assert "AFTER" in NFL.season_phase("2027-01-17")["why"]
    # the slug's own date (the America/New_York game day)
    assert NFL.phase_of_slug("aec-nfl-det-car-2026-10-04")["phase"] == \
        NFL.PHASE_REGULAR
    assert NFL.phase_of_slug("aec-nfl-ind-was-2026-08-15")["phase"] is None
    assert NFL.phase_of_slug("aec-nfl-ind-was")["phase"] is None


# ═════════════════════════════════════════════════════════════════════
# 3 · THE CONVERSION: NAMED REFUSALS, AND NOTHING OUTSIDE THE NFL
# ═════════════════════════════════════════════════════════════════════

def test_conversion_refuses_by_name():
    kw = dict(sport_family="football", league="nfl", phase=NFL.PHASE_REGULAR)
    assert NFL.convert(0.6, venue_rules_text="", **kw)["refusal"] == \
        NFL.R_VENUE_TEXT_ABSENT
    assert NFL.convert(0.6, venue_rules_text="Overtime is included if "
                       "played.", **kw)["refusal"] == NFL.R_VENUE_TIE_NOT_STATED
    assert NFL.convert(0.6, venue_rules_text=NFL_TEXT.replace(
        "settle to $0.50", "resolve to No"), **kw)["refusal"] == \
        NFL.R_VENUE_TIE_NOT_HALF
    assert NFL.convert(0.6, venue_rules_text=NFL_TEXT,
                       book_outcome_names=["A", "Draw", "B"],
                       **kw)["refusal"] == NFL.R_BOOK_PRICES_DRAW
    ok = NFL.convert(0.6, venue_rules_text=NFL_TEXT,
                     book_outcome_names=["A", "B"], **kw)
    assert ok["refusal"] is None and ok["applies"] is True
    assert ok["p"] < 0.6


@pytest.mark.parametrize("text", [
    # a contradicting tie clause APPENDED to the cited text (review probe)
    NFL_TEXT + " If the game ends in a tie after overtime, all positions "
               "resolve to No.",
    # an asymmetric payout that starts with the cited words
    NFL_TEXT.replace("settle to $0.50.", "settle to $0.50 for Yes and 1.00 "
                                         "for No."),
    NFL_TEXT.replace("settle to $0.50.", "settle to $0.50 per share to the "
                                         "home team."),
    # a variant amount, and a draw clause beside the tie clause
    NFL_TEXT.replace("settle to $0.50.", "settle to $0.45."),
    NFL_TEXT + " In the event of a draw, the market resolves to the home "
               "team.",
    # the cited sentence twice is still not ONE tie clause
    NFL_TEXT + " " + NFL.Q_VENUE_TIE,
])
def test_a_tie_clause_that_is_not_exactly_the_cited_one_is_refused(text):
    vt = NFL.venue_tie_payout(text)
    assert vt["payout"] is None and vt["refusal"] == NFL.R_VENUE_TIE_NOT_HALF
    got = NFL.convert(0.6, sport_family="football", league="nfl",
                      venue_rules_text=text, phase=NFL.PHASE_REGULAR)
    assert got["p"] is None and got["refusal"] == NFL.R_VENUE_TIE_NOT_HALF


def test_the_cited_tie_sentence_is_read_wherever_it_ends_the_text():
    assert NFL.venue_tie_payout(NFL_TEXT)["payout"] == 0.5
    # the venue's own text on every captured listing
    for m in NFL_LISTING["markets"] + SNF_LISTING["markets"]:
        assert NFL.venue_tie_payout(m["description"])["payout"] == 0.5
    # the sentence closing the text without its full stop
    assert NFL.venue_tie_payout(
        "Overtime is included if played. " + NFL.Q_VENUE_TIE[:-1]
    )["payout"] == 0.5
    # no tie word anywhere: not stated (a different refusal)
    assert NFL.venue_tie_payout("Overtime is included if played.")[
        "refusal"] == NFL.R_VENUE_TIE_NOT_STATED


def test_conversion_changes_nothing_outside_the_nfl():
    for fam, lg in (("baseball", None), ("soccer", None),
                    ("football", "cfb")):
        got = NFL.convert(0.61, sport_family=fam, league=lg,
                          venue_rules_text=NFL_TEXT)
        assert got["applies"] is False and got["p"] == 0.61
    pin = {"p": 0.61}
    assert PB.apply_venue_conversion(pin, {"venue_conversion": None}) is None
    assert pin == {"p": 0.61}


# ═════════════════════════════════════════════════════════════════════
# 4 · SETTLEMENT AS PAYOUTS: WHAT THE STRICT POLICY STILL LACKS
# ═════════════════════════════════════════════════════════════════════

def test_the_tie_and_the_postponement_are_named_payout_mismatches():
    a = vset.attest(sport_family="football",
                    venue_evidence={"rules_text": NFL_TEXT},
                    book_evidence={"outcome_names": ["A", "B"]})
    draw = a["rules"]["draw"]
    assert draw["refusal"] == vset.R_DRAW_ASYMMETRIC
    tie = draw["tie_payout_comparison"]
    assert tie["book_payout"] == T.PAY_STAKE_BACK
    assert tie["venue_payout"] == "PAYS_0.50_PER_CONTRACT"
    assert tie["book_cite"]["quote"] == NFL.Q_BOOK_TIE
    assert "no money-line rule" not in draw["detail"]     # cand24 corrected
    b = vset.settlement_blockers(a)
    assert b["established"] is False
    assert ("SETTLEMENT_TERMS_INCOMPATIBLE:TIE_AFTER_OVERTIME(book=%s;"
            "venue=PAYS_0.50_PER_CONTRACT)" % T.PAY_STAKE_BACK) in b["blockers"]
    assert any(x.startswith("SETTLEMENT_TERMS_INCOMPATIBLE:" + T.C_NOT_PLAYED)
               for x in b["blockers"]), b["blockers"]
    missing = {m["condition"] for m in NFL.STRICT_POLICY_MISSING}
    assert missing == {"TIE_AFTER_OVERTIME", "POSTPONED_OR_NOT_RESCHEDULED",
                       "SUSPENDED_CALLED_OR_STOPPED_EARLY"}


def test_the_state_table_compares_payouts():
    st = {s["state"]: s for s in NFL.SETTLEMENT_STATES}
    assert st["TIE_AFTER_OVERTIME"]["venue_payout"] == 0.5
    assert st["TIE_AFTER_OVERTIME"]["class"] == NFL.ORDINARY
    for k in ("POSTPONED_RESCHEDULED_WITHIN_TWO_WEEKS",
              "POSTPONED_NOT_RESCHEDULED_WITHIN_TWO_WEEKS",
              "SUSPENDED_OR_ABANDONED", "VENUE_CHANGED"):
        assert st[k]["class"] == NFL.EXCEPTIONAL
    assert st["PRO_BOWL_OR_EXHIBITION"]["class"] == NFL.EXCLUDED
    rec = NFL.states_record()
    assert rec["exceptional_probability"] == "UNMEASURED"


# ═════════════════════════════════════════════════════════════════════
# 5 · THE COMPLETED-GAME MATCH
# ═════════════════════════════════════════════════════════════════════

def _row(slug, text, odds=None):
    return {"us_market_slug": slug, "sport_family": "football",
            "settlement_comparison": {"venue_rules_text": text},
            "raw_odds": odds or {"A": 1.5, "B": 2.6}}


def _cand(slug):
    return {"us_market_slug": slug, "sport_family": "football",
            "market": "h2h", "line": None, "period": "FULL_GAME",
            "payout_event": "A", "side": "ORDER_INTENT_BUY_LONG",
            "fixture": "fx", "refusals": [], "settlement": {},
            "pinnacle": {}}


def test_nfl_terms_are_established_from_the_venues_own_text():
    m = PB.completed_game_match(_cand("aec-nfl-ind-was-2026-10-04"),
                                _row("aec-nfl-ind-was-2026-10-04", NFL_TEXT))
    names = {c["check"]: c for c in m["checks"]}
    assert names["ordinary_completion_grading_period"]["passed"]
    assert names["nfl_tie_priced_from_cited_evidence"]["passed"]
    assert names["nfl_fixture_date_matches_the_venue_slug"]["passed"]
    assert names["nfl_regular_season_fixture_established"]["passed"]
    assert m["venue_conversion"]["league"] == "nfl"
    assert m["venue_conversion"]["phase"] == NFL.PHASE_REGULAR
    assert PB.R_FAMILY not in m["refusals"]
    # (this synthetic candidate carries no payout-outcome evidence, so only
    # the NFL checks are asserted here; the paper pass proves the ENTER)
    assert not set(NFL.REFUSALS) & set(m["refusals"]), m["refusals"]


def test_the_college_board_takes_its_own_terms_never_the_nfls():
    """PIN UPDATED IN THE P0 INCIDENT (NCAAF stream). This test pinned the
    college board at NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT because nothing
    had cited its contract. That fact legitimately changed: the college
    contract is now compared clause by clause from its OWN cited texts
    (bettor_ncaaf_settlement; tests/test_ncaaf_settlement_evidence.py). What
    this test protected still holds and is asserted: the college board never
    borrows the NFL's terms -- its grading period is the college one, no NFL
    check runs on it, no NFL tie conversion is carried, and the college text
    on an NFL slug does not establish the NFL grading."""
    cfb = CFB_LISTING["markets"][0]
    m = PB.completed_game_match(_cand(cfb["slug"]),
                                _row(cfb["slug"], cfb["description"]))
    assert PB.R_FAMILY not in m["refusals"], m["refusals"]
    names = {c["check"]: c for c in m["checks"]}
    gp = names["ordinary_completion_grading_period"]
    assert gp["passed"] and gp["book"]["period"] == PB.GP_FOOTBALL_NCAAF
    assert gp["book"]["period"] != PB.GP_FOOTBALL_NFL
    assert not any(n.startswith("nfl_") for n in names), sorted(names)
    assert m["venue_conversion"]["league"] == "cfb"
    assert "phase" not in m["venue_conversion"]
    assert not set(NFL.REFUSALS) & set(m["refusals"]), m["refusals"]
    nfl_slug = "aec-nfl-ind-was-2026-10-04"
    on_nfl = PB.completed_game_match(_cand(nfl_slug),
                                     _row(nfl_slug, cfb["description"]))
    assert PB.R_GP_UNKNOWN in on_nfl["refusals"], on_nfl["refusals"]


def test_pro_bowl_and_mis_dated_slugs_are_refused():
    pro = NFL_TEXT.replace("Indianapolis Colts vs Washington Commanders",
                           "AFC vs NFC").replace("NFL game", "NFL Pro Bowl "
                                                 "game")
    m = PB.completed_game_match(_cand("aec-nfl-afc-nfc-2026-10-04"),
                                _row("aec-nfl-afc-nfc-2026-10-04", pro))
    assert NFL.R_EXHIBITION in m["refusals"]
    m = PB.completed_game_match(_cand("aec-nfl-ind-was-2026-10-05"),
                                _row("aec-nfl-ind-was-2026-10-05", NFL_TEXT))
    assert NFL.R_DATE_INCONSISTENT in m["refusals"]
    # a PRESEASON game worded exactly like the regular season (no marker
    # word anywhere): refused because its phase is not established
    pre = NFL_TEXT.replace("Oct 4, 2026", "Aug 15, 2026")
    m = PB.completed_game_match(_cand("aec-nfl-ind-was-2026-08-15"),
                                _row("aec-nfl-ind-was-2026-08-15", pre))
    assert NFL.exhibition_marker("aec-nfl-ind-was-2026-08-15", pre) is None
    assert NFL.R_PHASE_NOT_REGULAR in m["refusals"], m["refusals"]
    assert m["venue_conversion"]["phase"] is None
    # a playoff-window game: not established either
    post = NFL_TEXT.replace("Oct 4, 2026", "Jan 17, 2027")
    m = PB.completed_game_match(_cand("aec-nfl-ind-was-2027-01-17"),
                                _row("aec-nfl-ind-was-2027-01-17", post))
    assert NFL.R_PHASE_NOT_REGULAR in m["refusals"], m["refusals"]
    # the Sunday-night game: slug / text / kickoff on the ET day agree
    snf = SNF_LISTING["markets"][0]
    k = datetime.fromisoformat(snf["gameStartTime"].replace("Z", "+00:00"))
    fd = NFL.fixture_date(slug=snf["slug"], venue_rules_text=snf["description"],
                          kickoff_epoch=k.timestamp())
    assert fd["refusal"] is None and fd["event_date"] == "2026-10-04"
    assert fd["kickoff_utc_date"] == "2026-10-05"


# ═════════════════════════════════════════════════════════════════════
# 6 · PINNAPI: SPORT 5, THE PERIOD-0 TWO-WAY LINE
# ═════════════════════════════════════════════════════════════════════

AT = 1791150000.0


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _football_cache(prices):
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open", "prices": prices}
    start = AT + 3600
    event = {"id": 77, "startTime": _iso(start), "isLive": False,
             "participants": [{"name": "Carolina Panthers", "alignment": "home"},
                              {"name": "Detroit Lions", "alignment": "away"}],
             "markets": [market]}
    c = F.FeedCache()
    e = c.new_connection([("prematch", 5)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": (AT - 10) * 1000, "events": [event]}, epoch=e,
            received_ms=(AT - 10) * 1000 + 5)
    # a snapshot alone never makes a price current: an observed change does
    moved = json.loads(json.dumps(market))
    moved["prices"][0]["price"] += 5
    c.apply({"type": "prematch_markets", "matchup_id": 77, "data": [moved],
             "sport_id": 5, "ts": (AT - 2) * 1000}, epoch=e,
            received_ms=(AT - 2) * 1000 + 5)
    disc = {"id": "disc-77", "home_team": "Carolina Panthers",
            "away_team": "Detroit Lions", "commence_time": _iso(start),
            "bookmakers": []}
    return c, disc


def test_pinnapi_selects_the_nfl_two_way_game_line():
    c, disc = _football_cache([{"designation": "home", "price": 170},
                               {"designation": "away", "price": -200}])
    q = P.select(c, disc, None, family="football", sharp_books=set(), at=AT,
                 runtime_id="r1")
    assert q is not None and q["reference_input"]["provider"] == P.PROVIDER
    assert set(q["prices"]) == {"Carolina Panthers", "Detroit Lions"}
    v = devig.valuation(
        # the lane's contract carries the venue-native slug
        # (ext_pinnacle_loop), which is how the de-vig knows it is the NFL
        contract={"sport_family": "football", "market": "h2h",
                  "selection": "Detroit Lions", "event_key": "disc-77",
                  "us_market_slug": "aec-nfl-det-car-2026-10-04",
                  "period": "FULL_GAME", "line": None},
        quote={"book": "pinnacle", "outcomes": q["prices"],
               "event_key": "disc-77", "period": "FULL_GAME", "line": None,
               "observed_at": q["observed_at"],
               "received_at": q["received_at"]}, now=AT)
    assert v["probability"] is not None and v["expected_outcomes"] == 2
    assert v["conditional_on"]["condition"] == "NO_TIE"


def test_pinnapi_refuses_a_draw_priced_football_line():
    c, disc = _football_cache([{"designation": "home", "price": 170},
                               {"designation": "away", "price": -200},
                               {"designation": "draw", "price": 4000}])
    fallback = {"prices": {"x": 2.0}, "reference_input": {}}
    q = P.select(c, disc, fallback, family="football", sharp_books=set(),
                 at=AT, runtime_id="r1")
    assert q["reference_input"]["fallback_reason"] == \
        P.R_FOOTBALL_DRAW_PRICED


def test_the_census_admits_every_production_nfl_row_and_no_college_row():
    """THE PRODUCTION OBJECT (audit item 7). Every current NFL catalogue row
    production persisted carries the question's clock minutes as its line
    ('5:00 PM' -> '00'); family_of proves that artifact with the existing
    side-aware clock proof and admits the money line -- for the NFL only."""
    rows = PROD_ROWS["rows"]
    assert len(rows) == 28
    assert all(r["line"] not in (None, "") for r in rows)
    for r in rows:
        got = CEN.family_of(r["kind"], r["line"], r["sports_type"], r)
        assert got == ("MONEYLINE", True, "PROVED_CLOCK_ARTIFACT"), (r, got)
    r = rows[0]
    # with the leagues disagreeing, the league absent, or a league no one
    # measured, the type alone admits nothing. PIN UPDATED IN THE P0 INCIDENT
    # (NCAAF stream): a consistent COLLEGE row (structured league and slug
    # both cfb) is admitted now, by the college board's own measurement and
    # cited terms -- its production rows are pinned in
    # tests/test_ncaaf_settlement_evidence.py -- and the refusal code, which
    # said "NFL only", says what it means.
    for over in ({"team_league": "cfb"},
                 {"team_league": "cfl",
                  "identifier": "aec-cfl-ala-aub-2026-10-04"},
                 {"team_league": None, "identifier": None}):
        got = CEN.family_of(r["kind"], r["line"], r["sports_type"],
                            dict(r, **over))
        assert got == ("MONEYLINE", False,
                       CEN.R_FOOTBALL_LEAGUE_NOT_ADMITTED), over
    got = CEN.family_of(r["kind"], r["line"], r["sports_type"],
                        dict(r, team_league="cfb",
                             identifier="aec-cfb-ala-aub-2026-10-04"))
    assert got == ("MONEYLINE", True, "PROVED_CLOCK_ARTIFACT")
    # a REAL line the side states is never erased by the clock proof
    got = CEN.family_of(r["kind"], "3.5", r["sports_type"], r)
    assert got == ("UNKNOWN", False, "WINNER_WITH_UNEXPECTED_LINE")
    # baseball and soccer are untouched
    assert CEN.family_of("side", None, "baseball_team_full_game_winner") == \
        ("MONEYLINE", True, None)
    assert CEN.family_of("side", None, "soccer_team_full_time_winner") == \
        ("MONEYLINE", True, None)


def _held_cache(start, prices):
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open", "prices": prices}
    at = start - 3600
    ev = {"id": 77, "startTime": _iso(start), "isLive": False,
          "participants": [{"name": "Carolina Panthers", "alignment": "home"},
                           {"name": "Detroit Lions", "alignment": "away"}],
          "markets": [market]}
    c = F.FeedCache()
    e = c.new_connection([("prematch", 5)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": (at - 60) * 1000, "events": [ev]}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    moved = json.loads(json.dumps(market))
    moved["prices"][0]["price"] += 5
    c.apply({"type": "prematch_markets", "matchup_id": 77, "data": [moved],
             "sport_id": 5, "ts": (at - 2) * 1000}, epoch=e,
            received_ms=(at - 2) * 1000 + 5)
    return c, at


class _CatalogueConn:
    """The reads the held watch makes -- the held slugs, then the two
    catalogue reads of held_event_id -- answered from the production rows
    (an explicit fake input; no database)."""

    def __init__(self, row, event_rows):
        self.row, self.event_rows = row, event_rows

    async def fetchrow(self, sql, slug):
        return None if self.row is None else dict(self.row)

    async def fetch(self, sql, *args):
        if "paper_fills" in sql:              # pinnapi_held.HELD_SLUGS_SQL
            return [{"slug": self.row["identifier"], "kind": "PAPER"}]
        return [dict(r) for r in self.event_rows]


def test_a_held_nfl_contract_is_read_from_the_feed_end_to_end(monkeypatch):
    """R30A review: every held NFL contract was MATCHED_UNSUPPORTED_FAMILY,
    so neither the held read nor the held-event watch ever answered for NFL
    inventory. On the PRODUCTION catalogue rows of the Sunday-night game,
    with football subscribed and a fresh period-0 two-way quote, both now
    answer: the event is the one provider event, and the read is the book's
    P(win | no tie) with its conditioning carried for the converting
    reader."""
    import types
    rows = [r for r in PROD_ROWS["rows"]
            if r["identifier"] == "aec-nfl-det-car-2026-10-04"]
    lions = next(r for r in rows if r["side_norm"] == "lions")
    cache, at = _held_cache(lions["game_start"],
                            [{"designation": "home", "price": 170},
                             {"designation": "away", "price": -200}])
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
        cache=cache, sport_ids=[5]))
    view = CEN.feed_event_view(cache)
    got = FR.held_quote(lions, event_rows=rows, payout_event="Detroit Lions",
                        payout_is_complement=False, at=at, max_age_s=30.0,
                        sport_ids=[5], synced=True, view=view)
    assert got["ok"] is True, got
    assert got["sport_id"] == 5 and got["designation"] == "away"
    dv = got["devig"]
    assert got["p"] == pytest.approx(dv["devigged"]["away"])
    assert dv["outcomes"] == 2 and set(dv["raw_odds"]) == {"home", "away"}
    assert got["conditional_on"]["condition"] == "NO_TIE"
    # the complement side reads 1 - p
    nc = FR.held_quote(lions, event_rows=rows,
                       payout_event="NOT(Detroit Lions)",
                       payout_is_complement=True, at=at, max_age_s=30.0,
                       sport_ids=[5], synced=True, view=view)
    assert nc["ok"] and nc["p"] == pytest.approx(1.0 - got["p"])
    # the 30 s rule is unchanged: 31 s later the same quote is refused
    stale = FR.held_quote(lions, event_rows=rows,
                          payout_event="Detroit Lions",
                          payout_is_complement=False, at=at + 31.0,
                          max_age_s=30.0, sport_ids=[5], synced=True,
                          view=view)
    assert stale["ok"] is False
    # the held-event watch targets the NFL event...
    eid, why = asyncio.run(FR.held_event_id(
        _CatalogueConn(lions, rows), lions["identifier"]))
    assert (eid, why) == (77, None)
    from sportsassets import pinnapi_held as PH
    watch = PH.HeldWatch(clock=lambda: at)
    got_w = asyncio.run(PH.refresh(_CatalogueConn(lions, rows), watch=watch))
    assert got_w["ok"] and watch.is_held(77), got_w
    # ...so the existing reactive scheduler serves the held NFL event's
    # change FIRST (open inventory), ahead of hot and discovery work
    s = R.Scheduler(_Cache(), None, None, clock=lambda: at, held=watch)
    s.seeds[77] = {"event": {"commence_time": _iso(lions["game_start"])},
                   "sport_key": "americanfootball_nfl", "family": "football",
                   "received_at": at, "registered_at": at}
    s.seeds["mlb"] = {"event": {"commence_time": _iso(at + 3600)},
                      "sport_key": "baseball_mlb", "family": "baseball",
                      "received_at": at, "registered_at": at}
    s.changed(_Q("mlb", 1))
    s.changed(_Q(77, 2))
    assert s.counts["HELD_QUEUED"] == 1
    assert s.next_job()[0] == 77
    # football not subscribed (production's scope today is [6, 1])
    off = FR.held_quote(lions, event_rows=rows, payout_event="Detroit Lions",
                        payout_is_complement=False, at=at, max_age_s=30.0,
                        sport_ids=[6, 1], synced=True, view=view)
    assert off["ok"] is False and off["reason"] == CEN.S_OUT_OF_SCOPE


def test_a_held_college_contract_is_read_and_an_unmeasured_league_is_not(
        monkeypatch):
    """PIN UPDATED IN THE P0 INCIDENT (NCAAF stream). This test pinned a held
    COLLEGE contract as unsupported because the college board had no
    measurement and no cited terms. Both now exist (SUPPORTED_BY_LEAGUE cfb,
    research-sql run 37241503567; bettor_ncaaf_settlement), so a held college
    contract is read like a held NFL one -- the NO_TIE conditioning travels
    with the number, and the reader (paper_benchmark.held_venue_conversion)
    re-checks the college contract's cited clauses. What the test protected
    still holds and is asserted: a football league NO ONE measured (the CFL
    here) stays unsupported at every held reader."""
    import types
    for league, admitted in (("cfb", True), ("cfl", False)):
        rows = [dict(r, team_league=league,
                     identifier="aec-%s-det-car-2026-10-04" % league,
                     event_slug="%s-det-car-2026-10-04" % league)
                for r in PROD_ROWS["rows"]
                if r["identifier"] == "aec-nfl-det-car-2026-10-04"]
        lions = next(r for r in rows if r["side_norm"] == "lions")
        cache, at = _held_cache(lions["game_start"],
                                [{"designation": "home", "price": 170},
                                 {"designation": "away", "price": -200}])
        monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
            cache=cache, sport_ids=[5]))
        got = FR.held_quote(lions, event_rows=rows,
                            payout_event="Detroit Lions",
                            payout_is_complement=False, at=at,
                            max_age_s=30.0, sport_ids=[5], synced=True,
                            view=CEN.feed_event_view(cache))
        eid, why = asyncio.run(FR.held_event_id(
            _CatalogueConn(lions, rows), lions["identifier"]))
        from sportsassets import pinnapi_held as PH
        watch = PH.HeldWatch(clock=lambda: at)
        asyncio.run(PH.refresh(_CatalogueConn(lions, rows), watch=watch))
        if admitted:
            assert got["ok"] is True, (league, got)
            assert got["conditional_on"]["condition"] == "NO_TIE"
            assert (eid, why) == (77, None)
            assert watch.is_held(77)
        else:
            assert got["ok"] is False and got["reason"] == CEN.S_UNSUPPORTED
            assert eid is None and why == CEN.S_UNSUPPORTED
            assert not watch.is_held(77)
            assert watch.unmatched == {lions["identifier"]:
                                       CEN.S_UNSUPPORTED}


def test_held_nfl_reads_use_the_de_vig_spelling_and_the_nfl_type():
    assert CEN.sport_family_of(5) == "football"
    # admitted for the NFL only; the family-wide set is unchanged
    assert ("football", "h2h") not in devig.SUPPORTED
    assert devig.expected_outcomes("football", "h2h", league="nfl") == 2
    # PIN UPDATED IN THE P0 INCIDENT: the college board is admitted by its
    # own measurement (research-sql run 37241503567); an unmeasured football
    # league is not
    assert devig.expected_outcomes("football", "h2h", league="cfb") == 2
    assert devig.expected_outcomes("football", "h2h", league="cfl") is None
    assert CEN.sport_id_of("football_team_full_game_winner") == 5
    assert "football_team_full_game_winner" in FR.HELD_FULL_GAME_TYPES


# ═════════════════════════════════════════════════════════════════════
# 7 · HOT NFL FRESHNESS PRIORITY (the existing scheduler, one more tier)
# ═════════════════════════════════════════════════════════════════════

class _Held:
    def __init__(self, held=()):
        self.held = set(held)

    def is_held(self, eid):
        return eid in self.held


class _Cache:
    def read(self, eid, key, evaluated_ms=None, max_age_s=None):
        return {"ok": True}


class _Q:
    key = F.FULL_GAME_MONEYLINE_KEY

    def __init__(self, eid, n):
        self.event_id, self.epoch, self.source_change_ms = eid, 1, n
        self.prices = {"home": 1.5 + n / 1000.0}
        self.received_ms = AT * 1000


def _sched(held=()):
    async def noop(*a, **k):
        return None
    s = R.Scheduler(_Cache(), noop, noop, clock=lambda: AT, held=_Held(held))
    return s


def _seed(s, eid, sport_key, start):
    s.seeds[eid] = {"event": {"commence_time": _iso(start)},
                    "sport_key": sport_key, "family": "football",
                    "received_at": AT, "registered_at": AT}


def test_held_then_hot_nfl_then_discovery():
    s = _sched(held={"held"})
    _seed(s, "disc", "baseball_mlb", AT + 3600)
    _seed(s, "hot", "americanfootball_nfl", AT + 1800)       # 30 min out
    _seed(s, "cold", "americanfootball_nfl", AT + 3 * 86400)  # days away
    _seed(s, "held", "baseball_mlb", AT + 3600)
    for i, eid in enumerate(("disc", "cold", "hot", "held")):
        s.changed(_Q(eid, i + 1))
    assert s.counts["HOT_QUEUED"] == 1 and s.counts["HELD_QUEUED"] == 1
    order = [s.next_job()[0] for _ in range(4)]
    assert order == ["held", "hot", "disc", "cold"]
    assert s.next_job() is None


def test_waiting_discovery_is_served_while_hot_nfl_events_keep_changing():
    """R30A review: strict hot priority could hold every MLB and soccer
    discovery change behind an NFL Sunday on the single worker. With hot
    changes arriving after EVERY job, a waiting discovery change is still
    evaluated within HOT_MAX_CONSECUTIVE + 1 jobs, every time."""
    s = _sched()
    hot = ["nfl%d" % i for i in range(12)]
    for i, eid in enumerate(hot):
        _seed(s, eid, "americanfootball_nfl", AT + 600 + i)
    disc = ["mlb%d" % i for i in range(5)] + ["epl%d" % i for i in range(3)]
    for eid in disc:
        _seed(s, eid, ("baseball_mlb" if eid.startswith("mlb")
                       else "soccer_epl"), AT + 3600)
    n = [0]

    def change(eid):
        n[0] += 1
        s.changed(_Q(eid, n[0]))

    for eid in hot + disc:
        change(eid)
    served, gaps, since = [], [], 0
    for _ in range(200):
        job = s.next_job()
        if job is None:
            break
        eid = job[0]
        served.append(eid)
        if eid in disc:
            gaps.append(since)
            since = 0
        else:
            since += 1
        # every hot event changes again after every job: the hot queue is
        # never empty while discovery waits
        for h in hot:
            change(h)
        if len([e for e in served if e in disc]) == len(disc):
            break
    assert [e for e in served if e in disc] == disc       # FIFO, all served
    assert max(gaps) <= R.HOT_MAX_CONSECUTIVE, gaps
    assert s.counts["HOT_YIELDED_TO_DISCOVERY"] >= len(disc) - 1
    # with no discovery waiting, hot runs back to back (it starves nobody)
    s2 = _sched()
    for i, eid in enumerate(hot[:5]):
        _seed(s2, eid, "americanfootball_nfl", AT + 600)
        s2.changed(_Q(eid, i + 1))
    assert [s2.next_job()[0] for _ in range(5)] == hot[:5]
    # held still goes first, before hot and discovery alike
    s3 = _sched(held={"h"})
    _seed(s3, "h", "baseball_mlb", AT + 3600)
    _seed(s3, "x", "americanfootball_nfl", AT + 600)
    _seed(s3, "d", "baseball_mlb", AT + 3600)
    for i, eid in enumerate(("d", "x", "h")):
        s3.changed(_Q(eid, i + 1))
    assert [s3.next_job()[0] for _ in range(3)] == ["h", "x", "d"]


def test_hot_window_and_eviction_pin():
    s = _sched()
    _seed(s, "live", "americanfootball_nfl", AT - 2 * 3600)   # in play
    _seed(s, "done", "americanfootball_nfl", AT - 6 * 3600)   # long over
    assert s._is_hot("live") and not s._is_hot("done")
    s.seed_cap = 1
    _seed(s, "other", "baseball_mlb", AT + 600)
    s._evict_seeds()
    assert "live" in s.seeds                    # hot seed pinned
    assert R.HOT_SPORT_KEYS == frozenset(("americanfootball_nfl",))


# ═════════════════════════════════════════════════════════════════════
# 8 · THE PAPER SETTLEMENT OF A TIE
# ═════════════════════════════════════════════════════════════════════

def test_a_tie_settles_at_the_stated_half_and_other_prices_are_unchanged():
    rows = [{"id": 1, "settlement_read": "0.5",
             "settlement_read_at": AT,
             "rules": NFL_TEXT}]
    for side in ("LONG", "SHORT"):
        got = PX.venue_price_settlement(rows, holding_side=side)
        assert got["price"] == 0.5
        # R30A review: the venue's NFL text states BOTH the $0.50 tie and
        # the last-fair-market-price clause, and the read is a price, not a
        # score: a tie and a postponed game priced at 0.50 are not told
        # apart, so neither the ordinary nor the exceptional class is
        # claimed. The payout is the same.
        assert got["settlement_state"] == NFL.S_TIE_OR_LAST_FAIR_PRICE
        assert got["state_class"] == NFL.AMBIGUOUS
        assert "final score" in got["would_distinguish"]
    # a text whose ONLY price settlement is the tie: the tie, by its terms
    tie_only = NFL_TEXT.replace(" " + NFL.Q_VENUE_POSTPONED, "")
    assert NFL.Q_VENUE_POSTPONED not in tie_only
    got = PX.venue_price_settlement([dict(rows[0], rules=tie_only)],
                                    holding_side="LONG")
    assert got["price"] == 0.5
    assert got["settlement_state"] == "TIE_AFTER_OVERTIME"
    assert got["state_class"] == NFL.ORDINARY
    # a non-tie price on an NFL contract is the last-fair-market-price clause
    rows[0]["settlement_read"] = "0.37"
    got = PX.venue_price_settlement(rows, holding_side="LONG")
    assert got["price"] == 0.37 and "settlement_state" not in got
    # a contract whose text states no tie rule is unchanged: 0.5 reads as
    # the last fair market price when that clause is stated
    mlb = ("This market will settle to the winner of the A vs B MLB game. "
           "Extra innings are included if played. " + NFL.Q_VENUE_POSTPONED)
    rows[0].update(settlement_read="0.5", rules=mlb)
    got = PX.venue_price_settlement(rows, holding_side="LONG")
    assert got["price"] == 0.5 and "settlement_state" not in got


def test_every_nfl_refusal_is_worded_and_attributed():
    """Each named NFL refusal reaches the operator in words and the lost-
    opportunity ledger as a CONTROL with an attribution -- never as an
    unexplained code (the phase refusal is new in the R30A review)."""
    from sportsassets import bettor_paper_ops as OPS
    from sportsassets.lost_opportunity import classify as LC
    for r in NFL.REFUSALS:
        assert r in OPS.REFUSAL_WORDS, r
        assert r in LC.CONTROL_CODES, r
        assert LC.ATTRIBUTION_OF.get(r), r
    assert LC.ATTRIBUTION_OF[NFL.R_PHASE_NOT_REGULAR] == "EXPLICIT_POLICY"
