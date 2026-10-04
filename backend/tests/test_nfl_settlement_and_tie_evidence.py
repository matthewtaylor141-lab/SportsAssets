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
  8 THE PAPER SETTLEMENT READS A TIE AS THE CONTRACT'S STATED 0.50.
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
        assert table[str(sid)].lower().startswith(
            {"baseball": "baseball", "soccer": "soccer",
             "football": "football"}[fam]), (fam, sid)
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
    fav = NFL.venue_value(0.70)
    assert fav["tie_rate_end_used"] == "HIGHEST"
    assert fav["p"] == pytest.approx(0.70 - iv["hi"] * 0.20)
    dog = NFL.venue_value(0.30)
    assert dog["tie_rate_end_used"] == "LOWEST"
    # phase not established: a postseason game cannot tie -> lower end 0
    assert dog["tie_rate_used"] == 0.0 and dog["p"] == pytest.approx(0.30)
    reg = NFL.venue_value(0.30, phase=NFL.PHASE_REGULAR)
    assert reg["tie_rate_used"] == pytest.approx(iv["lo"])
    assert reg["p"] == pytest.approx(0.30 + iv["lo"] * 0.20)
    # the worst case is never above either end's value
    for p in (0.05, 0.3, 0.5, 0.62, 0.95):
        vv = NFL.venue_value(p)
        assert vv["p"] <= min(vv["p_venue_at_interval_ends"]) + 1e-15


# ═════════════════════════════════════════════════════════════════════
# 3 · THE CONVERSION: NAMED REFUSALS, AND NOTHING OUTSIDE THE NFL
# ═════════════════════════════════════════════════════════════════════

def test_conversion_refuses_by_name():
    kw = dict(sport_family="football", league="nfl")
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
    assert m["venue_conversion"]["league"] == "nfl"
    assert PB.R_FAMILY not in m["refusals"]


def test_the_college_board_is_left_where_it_was():
    cfb = CFB_LISTING["markets"][0]
    m = PB.completed_game_match(_cand(cfb["slug"]),
                                _row(cfb["slug"], cfb["description"]))
    assert PB.R_FAMILY in m["refusals"]
    assert m["venue_conversion"] is None


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


def test_held_nfl_reads_use_the_de_vig_spelling_and_the_nfl_type():
    assert CEN.sport_family_of(5) == "football"
    # admitted for the NFL only; the family-wide set is unchanged
    assert ("football", "h2h") not in devig.SUPPORTED
    assert devig.expected_outcomes("football", "h2h", league="nfl") == 2
    assert devig.expected_outcomes("football", "h2h", league="cfb") is None
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
        assert got["settlement_state"] == "TIE_AFTER_OVERTIME"
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
