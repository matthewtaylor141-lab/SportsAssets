"""Adriana's arbitrage engine: payoffs proved, prices compared last, no authority.

Every number asserted below is hand-computed in the comment beside it. The
fee arithmetic is checked against the repository's own fee modules
(`kalshi_orders.fee_for`, `bettor_fee_schedule.for_date`), and a mutant
engine with fees zeroed is shown to be caught by this suite.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from sportsassets import bettor_fee_schedule as FS
from sportsassets.agents import adriana_arb as A

SRC_PATH = pathlib.Path(A.__file__)
SRC = SRC_PATH.read_text()

NOW = datetime(2026, 10, 5, 18, 0, 0, tzinfo=timezone.utc)
W = ("2026-10-05T23:00:00+00:00", "2026-10-06T06:00:00+00:00")
EK = "NBA-LAL-BOS-20261005"
HALF = D("0.5")


# ── builders ─────────────────────────────────────────────────────────

def spec(subject, *, ek=EK, family=A.MONEYLINE, period="FULL_GAME_INCL_OT",
         line=None, source="NBA_OFFICIAL_BOXSCORE", window=W,
         void="VOID_PAYS_HALF", tie=A.TIE_IMPOSSIBLE):
    return A.SettlementSpec(ek, family, period, subject, line, source, window,
                            void, tie)


def ml_space(**kw):
    base = dict(event_key=EK, outcomes=("LAL", "BOS", "VOID", "POSTPONED"),
                exhaustive=True, basis="NBA moneyline: no ties, OT played")
    base.update(kw)
    return A.OutcomeSpace(**base)


def yes_lal(venue=A.KALSHI, market="K-LAL", **kw):
    return A.Contract(venue, market, A.YES, spec("LAL", **kw),
                      {"LAL": 1, "BOS": 0, "VOID": HALF, "POSTPONED": HALF},
                      sport="BASKETBALL")


def yes_bos(venue=A.POLYMARKET_US, market="P-BOS", **kw):
    return A.Contract(venue, market, A.YES, spec("BOS", **kw),
                      {"LAL": 0, "BOS": 1, "VOID": HALF, "POSTPONED": HALF},
                      sport="BASKETBALL")


def book(c, asks, age_s=0.0, now=NOW):
    return A.Book(c.venue, c.market_id, c.side, tuple(asks),
                  now - timedelta(seconds=age_s))


def codes(rec):
    return A.reason_codes(rec)


def positive_inputs():
    a, b = yes_lal(), yes_bos()
    return a, b, [book(a, [(D("0.45"), 100)], 3), book(b, [(D("0.45"), 100)], 1)]


def check_positive_case():
    """THE KNOWN GUARANTEED CASE.

    Kalshi YES LAL @0.45 x100, Polymarket US YES BOS @0.45 x100, books 3 s and
    1 s old (skew 2 s), slippage 0.01 / contract / leg, on 2026-10-05.
      Kalshi fee  ceil_cent(0.07 x 100 x 0.45 x 0.55 = 1.7325)     = 1.74
      PMUS fee    bankers(0.0695 x 100 x 0.2475 = 1.720125)        = 1.72
      cost = 45 + 45 + 2 x 1.00 slippage + 1.74 + 1.72              = 95.46
      payout = 100 x 1 (every outcome sums to exactly 1)            = 100
      net = 4.54, edge 0.0454 per contract, Q = 100 (whole depth)
    """
    a, b, books = positive_inputs()
    r = A.evaluate_pair(a, b, books, ml_space(), NOW)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS, codes(r)
    assert r["structure_kind"] == A.COMPLEMENT
    e = r["economics"]
    assert e["qty"] == 100
    assert [lg["fee"] for lg in e["legs"]] == ["1.74", "1.72"]
    assert e["total_cost"] == "95.46"
    assert e["worst_case_net_profit"] == "4.54"
    assert e["edge_per_contract"] == "0.0454"
    return r


# ═════════════════════════════════════════════════════════════════════
# THE GENUINE POSITIVE CASE
# ═════════════════════════════════════════════════════════════════════

def test_positive_complement_is_guaranteed_with_hand_computed_numbers():
    r = check_positive_case()
    assert r["reasons"] == []
    assert r["inputs"]["skew_s"] == 2.0
    assert [b["age_s"] for b in r["inputs"]["books"]] == [3.0, 1.0]
    assert r["economics"]["payout_by_outcome"] == {
        "LAL": "1", "BOS": "1", "VOID": "1.0", "POSTPONED": "1.0"}
    json.dumps(r)                                    # dict-serialisable


def test_the_record_is_json_and_carries_the_input_echo():
    a, b, books = positive_inputs()
    r = A.evaluate_pair(a, b, books, ml_space(), NOW)
    back = json.loads(json.dumps(r))
    legs = back["inputs"]["books"]
    assert {(x["venue"], x["market_id"]) for x in legs} == {
        (A.KALSHI, "K-LAL"), (A.POLYMARKET_US, "P-BOS")}
    assert legs[0]["observed_at"] == (NOW - timedelta(seconds=3)).isoformat()
    assert back["mode"] == "SHADOW"


# ═════════════════════════════════════════════════════════════════════
# SETTLEMENT AND PAYOFF EQUIVALENCE
# ═════════════════════════════════════════════════════════════════════

def test_classify_pair_equivalent_complementary_structure():
    sp = ml_space()
    assert A.classify_pair(yes_lal(), yes_bos(), sp)["relation"] == "COMPLEMENTARY"
    assert A.classify_pair(yes_lal(), yes_lal(A.POLYMARKET_US, "P-LAL"),
                           sp)["relation"] == "EQUIVALENT"
    odd = replace(yes_bos(), payoff={"LAL": 0, "BOS": 1, "VOID": 1,
                                     "POSTPONED": HALF})
    c = A.classify_pair(yes_lal(), odd, sp)
    assert c["relation"] == "STRUCTURE" and c["floor"] == "1"
    r = A.classify_pair(yes_lal(), yes_bos(source="ESPN"), sp)
    assert r["relation"] == A.REFUSED
    assert [x["code"] for x in r["reasons"]] == [A.SETTLEMENT_SOURCE_DIFFERS]


def test_void_mismatch_makes_a_lookalike_complement_refuse():
    # LAL YES pays 0.5 on VOID, the "other venue" BOS YES pays 0 on VOID
    # (a last-price / refund settlement mislabelled with the same rule text).
    # VOID sums to 0.5: floor 0.5 <= 0.45 + 0.45 = 0.90 raw asks.
    b = replace(yes_bos(), payoff={"LAL": 0, "BOS": 1, "VOID": 0,
                                   "POSTPONED": HALF})
    a = yes_lal()
    books = [book(a, [(D("0.45"), 100)]), book(b, [(D("0.45"), 100)])]
    r = A.evaluate_pair(a, b, books, ml_space(), NOW)
    assert r["verdict"] == A.REFUSED
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]
    assert r["payoff_table"]["floor_outcomes"] == ["VOID"]
    assert r["prices_compared"] is False
    r = A.evaluate_pair(a, b, books, ml_space(), NOW, expect_kind=A.COMPLEMENT)
    assert codes(r) == [A.PAYOFF_NOT_COMPLEMENTARY]
    assert "VOID" in r["reasons"][0]["detail"]


def test_a_tie_outcome_makes_a_two_way_lookalike_refuse():
    # NFL: ties exist. "LAL wins" YES and "BOS wins" YES both pay 0 on a TIE.
    sp = A.OutcomeSpace(EK, ("LAL", "BOS", "TIE", "VOID", "POSTPONED"),
                        exhaustive=True, tie_outcomes=("TIE",))
    a = A.Contract(A.KALSHI, "K-LAL", A.YES, spec("LAL", tie="TIE_RESOLVES_NO"),
                   {"LAL": 1, "BOS": 0, "TIE": 0, "VOID": HALF,
                    "POSTPONED": HALF})
    b = A.Contract(A.KALSHI, "K-BOS", A.YES, spec("BOS", tie="TIE_RESOLVES_NO"),
                   {"LAL": 0, "BOS": 1, "TIE": 0, "VOID": HALF,
                    "POSTPONED": HALF})
    books = [book(a, [(D("0.30"), 100)]), book(b, [(D("0.30"), 100)])]
    r = A.evaluate_pair(a, b, books, sp, NOW)
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]
    assert r["payoff_table"]["floor_outcomes"] == ["TIE"]
    # and a tie-possible contract over a space with no tie outcome refuses
    r = A.evaluate_pair(a, b, books, ml_space(), NOW)
    assert A.NONSTANDARD_OUTCOMES_MISSING in codes(r)


def test_postponed_mismatch_refuses():
    b = replace(yes_bos(), payoff={"LAL": 0, "BOS": 1, "VOID": HALF,
                                   "POSTPONED": 0})
    a = yes_lal()
    r = A.evaluate_pair(a, b, [book(a, [(D("0.4"), 10)]),
                               book(b, [(D("0.4"), 10)])], ml_space(), NOW)
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]
    assert r["payoff_table"]["floor_outcomes"] == ["POSTPONED"]


@pytest.mark.parametrize("change,code", [
    (dict(ek="NBA-OTHER"), A.EVENT_IDENTITY_UNPROVEN),
    (dict(period="FIRST_HALF"), A.MARKET_DEFINITION_DIFFERS),
    (dict(family=A.SPREAD), A.MARKET_DEFINITION_DIFFERS),
    (dict(source="ESPN"), A.SETTLEMENT_SOURCE_DIFFERS),
    (dict(window=(W[0], "2026-10-07T06:00:00+00:00")), A.SETTLE_WINDOW_DIFFERS),
    (dict(window=("2026-10-05T23:00:00", W[1])), A.SETTLE_WINDOW_INVALID),
    (dict(void="VOID_REFUNDS"), A.VOID_RULE_DIFFERS),
    (dict(tie="TIE_RESOLVES_NO_X"), A.TIE_RULE_DIFFERS),
])
def test_settlement_differences_each_have_a_precise_code(change, code):
    a = yes_lal()
    sp = ml_space(tie_outcomes=()) if "tie" not in change else \
        A.OutcomeSpace(EK, ("LAL", "BOS", "TIE", "VOID", "POSTPONED"),
                       exhaustive=True, tie_outcomes=("TIE",))
    if "tie" in change:
        pay_a = {"LAL": 1, "BOS": 0, "TIE": 0, "VOID": HALF, "POSTPONED": HALF}
        pay_b = {"LAL": 0, "BOS": 1, "TIE": 1, "VOID": HALF, "POSTPONED": HALF}
        a = A.Contract(A.KALSHI, "K-LAL", A.YES, spec("LAL", tie="TIE_RESOLVES_NO"),
                       pay_a)
        b = A.Contract(A.KALSHI, "K-BOS", A.YES, spec("BOS", **change), pay_b)
    else:
        b = yes_bos(**change)
    books = [book(a, [(D("0.45"), 100)]), book(b, [(D("0.45"), 100)])]
    r = A.evaluate_pair(a, b, books, sp, NOW)
    assert r["verdict"] == A.REFUSED
    assert code in codes(r), codes(r)
    assert r["economics"] is None and r["prices_compared"] is False


def test_settle_window_tolerance_is_explicit():
    a, b = yes_lal(), yes_bos(window=("2026-10-05T23:00:01+00:00", W[1]))
    books = [book(a, [(D("0.45"), 100)]), book(b, [(D("0.45"), 100)])]
    assert A.SETTLE_WINDOW_DIFFERS in codes(
        A.evaluate_pair(a, b, books, ml_space(), NOW))
    r = A.evaluate_pair(a, b, books, ml_space(), NOW, settle_window_tolerance_s=1)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS


@pytest.mark.parametrize("mutate,code", [
    (lambda c: replace(c, side="MAYBE"), A.SIDE_INVALID),
    (lambda c: replace(c, spec=replace(c.spec, resolution_source="")),
     A.CONTRACT_SPEC_INCOMPLETE),
    (lambda c: replace(c, spec=replace(c.spec, family="PARLAY")),
     A.CONTRACT_SPEC_INCOMPLETE),
    (lambda c: replace(c, market_id=""), A.CONTRACT_SPEC_INCOMPLETE),
    (lambda c: replace(c, spec=replace(c.spec, tie_rule="UNKNOWN")),
     A.TIE_RULE_UNKNOWN),
    (lambda c: replace(c, payoff={"LAL": 1, "BOS": 0, "VOID": HALF}),
     A.PAYOFF_INCOMPLETE),
    (lambda c: replace(c, payoff=dict(c.payoff, OTHER=0)),
     A.OUTCOME_SPACE_DIFFERS),
    (lambda c: replace(c, payoff=dict(c.payoff, VOID=0.5)), A.PAYOFF_INVALID),
    (lambda c: replace(c, payoff=dict(c.payoff, LAL=D("1.5"))),
     A.PAYOFF_INVALID),
])
def test_contract_validation_codes(mutate, code):
    a, b, books = positive_inputs()
    r = A.evaluate_pair(mutate(a), b, books, ml_space(), NOW)
    assert code in codes(r), codes(r)
    assert r["verdict"] == A.REFUSED


@pytest.mark.parametrize("space,code", [
    (ml_space(exhaustive=False), A.OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE),
    (ml_space(outcomes=("LAL", "BOS", "VOID")), A.NONSTANDARD_OUTCOMES_MISSING),
    (ml_space(outcomes=("LAL", "BOS", "BOS", "VOID", "POSTPONED")),
     A.DUPLICATE_OUTCOME),
    (ml_space(event_key=""), A.EVENT_IDENTITY_UNPROVEN),
    (None, A.OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE),
])
def test_outcome_space_codes(space, code):
    a, b, books = positive_inputs()
    r = A.evaluate_pair(a, b, books, space, NOW)
    assert code in codes(r), codes(r)


def test_equivalent_pair_is_not_a_hedge():
    # YES LAL on two venues: payoff 2 / 0 -> floor 0
    a, b = yes_lal(), yes_lal(A.POLYMARKET_US, "P-LAL")
    r = A.evaluate_pair(a, b, [book(a, [(D("0.2"), 9)]), book(b, [(D("0.2"), 9)])],
                        ml_space(), NOW)
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]


def test_duplicate_leg_and_parameter_and_clock_codes():
    a, b, books = positive_inputs()
    assert A.DUPLICATE_LEG in codes(
        A.evaluate_structure([a, a, b], books, ml_space(), NOW))
    assert codes(A.evaluate_structure([a], books, ml_space(), NOW)) == \
        [A.PARAMETER_INVALID]
    assert codes(A.evaluate_pair(a, b, books, ml_space(), NOW,
                                 slippage_per_contract=D("-0.01"))) == \
        [A.PARAMETER_INVALID]
    assert codes(A.evaluate_pair(a, b, books, ml_space(), NOW, max_qty=0)) == \
        [A.PARAMETER_INVALID]
    assert codes(A.evaluate_pair(a, b, books, ml_space(),
                                 NOW.replace(tzinfo=None))) == [A.CLOCK_INVALID]


def test_leg_alternatives_must_be_equivalent():
    a, b, books = positive_inputs()
    alt = replace(yes_lal(A.POLYMARKET_US, "P-LAL"),
                  payoff={"LAL": 1, "BOS": 0, "VOID": 0, "POSTPONED": HALF})
    r = A.evaluate_structure([(a, alt), b], books, ml_space(), NOW)
    assert A.LEG_ALTERNATIVES_NOT_EQUIVALENT in codes(r)


# ═════════════════════════════════════════════════════════════════════
# YES / NO AND SYNTHESIS
# ═════════════════════════════════════════════════════════════════════

def test_opposite_side_is_the_complement_of_the_same_market():
    a = yes_lal()
    n = A.opposite_side(a)
    assert n.side == A.NO and n.market_id == a.market_id
    assert A.classify_pair(a, n, ml_space())["relation"] == "COMPLEMENTARY"
    assert A.opposite_side(n).payoff == {k: D(1) - (D(1) - A._dec(v))
                                         for k, v in a.payoff.items()}


def test_no_ask_is_never_synthesised_by_default():
    no = A.opposite_side(yes_lal())
    ok, why = A.may_synthesize_no_ask(no)
    assert ok is False and "not declared" in why
    for venue in A.VENUES:
        assert A.may_synthesize_no_ask(replace(no, venue=venue))[0] is False
    with pytest.raises(A.RefusedError) as e:
        A.synthesize_no_asks(no, [(D("0.55"), 10)])
    assert e.value.code == A.NO_SIDE_NOT_LISTED
    assert A.may_synthesize_no_ask(yes_lal(),
                                   declared_single_instrument=[(A.KALSHI, "K-LAL")]
                                   )[0] is False          # a YES is not a NO
    asks = A.synthesize_no_asks(no, [(D("0.55"), 10), (D("0.56"), 5)],
                                declared_single_instrument=[(A.KALSHI, "K-LAL")])
    assert asks == ((D("0.44"), D(5)), (D("0.45"), D(10)))
    assert A.SINGLE_INSTRUMENT_BOOKS == frozenset()


# ═════════════════════════════════════════════════════════════════════
# FRESHNESS
# ═════════════════════════════════════════════════════════════════════

def _fresh(age_a, age_b, **kw):
    a, b = yes_lal(), yes_bos()
    books = [book(a, [(D("0.45"), 100)], age_a), book(b, [(D("0.45"), 100)], age_b)]
    return A.evaluate_pair(a, b, books, ml_space(), NOW, **kw)


def test_book_exactly_at_max_age_passes_and_one_microsecond_more_refuses():
    assert _fresh(30, 26)["verdict"] == A.GUARANTEED_AFTER_COSTS
    r = _fresh(30.000001, 26)
    assert codes(r) == [A.STALE_BOOK]
    assert _fresh(10, 6, max_age_s=10)["verdict"] == A.GUARANTEED_AFTER_COSTS
    assert A.STALE_BOOK in codes(_fresh(11, 6, max_age_s=10))


def test_skew_exactly_at_limit_passes_and_beyond_refuses():
    assert _fresh(6, 1)["verdict"] == A.GUARANTEED_AFTER_COSTS     # skew 5
    assert codes(_fresh(6.000001, 1)) == [A.UNSYNCHRONIZED_BOOKS]
    assert codes(_fresh(1, 3, max_skew_s=1)) == [A.UNSYNCHRONIZED_BOOKS]


def test_missing_naive_and_future_book_times_refuse():
    a, b = yes_lal(), yes_bos()
    naive = A.Book(a.venue, a.market_id, a.side, ((D("0.45"), 100),),
                   NOW.replace(tzinfo=None))
    none = A.Book(a.venue, a.market_id, a.side, ((D("0.45"), 100),), None)
    fut = book(a, [(D("0.45"), 100)], -1)
    bb = book(b, [(D("0.45"), 100)])
    for bk, code in ((naive, A.BOOK_TIME_MISSING), (none, A.BOOK_TIME_MISSING),
                     (fut, A.BOOK_TIME_IN_FUTURE)):
        assert codes(A.evaluate_pair(a, b, [bk, bb], ml_space(), NOW)) == [code]


def test_decision_code_reads_no_clock():
    for pat in (r"datetime\.now", r"utcnow", r"time\.time", r"date\.today",
                r"import time\b"):
        assert not re.search(pat, SRC), pat


def test_book_presence_identity_and_validity():
    a, b = yes_lal(), yes_bos()
    bb = book(b, [(D("0.45"), 100)])
    assert codes(A.evaluate_pair(a, b, [bb], ml_space(), NOW)) == [A.BOOK_MISSING]
    wrong = {a.key: book(yes_lal(market="K-OTHER"), [(D("0.45"), 100)]),
             b.key: bb}
    assert codes(A.evaluate_pair(a, b, wrong, ml_space(), NOW)) == \
        [A.BOOK_CONTRACT_MISMATCH]
    for asks in ([(D("1.2"), 10)], [(0.45, 10)], [(D("0.45"), -1)], [("x", 1)]):
        r = A.evaluate_pair(a, b, [book(a, asks), bb], ml_space(), NOW)
        assert codes(r) == [A.BOOK_INVALID], asks


def test_empty_or_fractional_only_depth_refuses():
    a, b = yes_lal(), yes_bos()
    bb = book(b, [(D("0.45"), 100)])
    for asks in ([], [(D("0.45"), D("0.5"))]):
        r = A.evaluate_pair(a, b, [book(a, asks), bb], ml_space(), NOW)
        assert codes(r) == [A.NO_EXECUTABLE_DEPTH], asks


# ═════════════════════════════════════════════════════════════════════
# FEES
# ═════════════════════════════════════════════════════════════════════

def test_kalshi_fee_matches_kalshi_orders_fee_for_over_a_grid():
    from sportsassets import kalshi_orders as KO
    for count in list(range(1, 41)) + [57, 99, 100, 250, 1000, 4321]:
        for cents in range(1, 100):
            p = D(cents) / 100
            assert A.kalshi_taker_fee(count, p) == KO.fee_for(count, p), (count, p)


def test_kalshi_rounding_is_per_order_not_per_contract():
    # p = 0.50: 0.0175 per contract. One contract rounds to 0.02; four
    # contracts in ONE order are 0.07 exactly, not 4 x 0.02 = 0.08.
    assert A.kalshi_taker_fee(1, HALF) == D("0.02")
    assert A.kalshi_taker_fee(4, HALF) == D("0.07")
    q = A.order_fee(A.KALSHI, [(HALF, 4)], at=NOW)
    assert q.known and q.fee == D("0.07")


def test_pmus_fee_is_the_dated_published_schedule():
    s = FS.for_date("2026-10-05")
    for n in (1, 7, 100, 333):
        for cents in (3, 21, 45, 50, 79, 97):
            p = D(cents) / 100
            q = A.order_fee(A.POLYMARKET_US, [(p, n)], at=NOW, sport="BASKETBALL")
            assert q.known and q.fee == s.taker_fee(n, p), (n, p)
    # multi-level, cumulative per order: banker's rounding of the summed exact
    fills = [(D("0.45"), 3), (D("0.46"), 4)]
    exact = sum(s.exact(s.theta_taker, n, p) for p, n in fills)
    assert A.order_fee(A.POLYMARKET_US, fills, at=NOW, sport="NBA").fee == \
        FS.bankers_cents(exact)


def test_pmus_schedule_change_day_charges_the_larger_candidate():
    at = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)   # before 04:00Z
    p, n = D("0.45"), 100
    want = max(FS.for_date("2026-09-25").taker_fee(n, p),
               FS.for_date("2026-09-24").taker_fee(n, p))
    assert A.order_fee(A.POLYMARKET_US, [(p, n)], at=at, sport="NBA").fee == want


def test_pmus_per_sport_theta_set_is_pinned_to_calibration_fees():
    from sportsassets import calibration_fees as CF
    default = CF.TAKER_BY_SPORT["DEFAULT"]
    own = {k for k, rows in CF.TAKER_BY_SPORT.items()
           if k != "DEFAULT" and rows != default}
    assert own == set(A.PMUS_SPORTS_WITH_OWN_THETA)
    assert FS.for_date("2026-10-05").theta_taker == CF.taker_coefficient(
        None, "2026-10-05T00:00:00Z")


@pytest.mark.parametrize("venue,sport,at", [
    (A.POLYMARKET, "BASKETBALL", NOW),
    ("SOME_EXCHANGE", "BASKETBALL", NOW),
    (A.POLYMARKET_US, None, NOW),
    (A.POLYMARKET_US, "Table Tennis", NOW),
    (A.POLYMARKET_US, "BASKETBALL", datetime(2026, 6, 1, tzinfo=timezone.utc)),
])
def test_unknown_fee_schedule_refuses_never_zero(venue, sport, at):
    q = A.order_fee(venue, [(D("0.45"), 10)], at=at, sport=sport)
    assert not q.known and q.fee is None and q.code == A.FEE_SCHEDULE_UNKNOWN
    a = yes_lal()
    b = replace(yes_bos(venue=venue), sport=sport)
    books = [book(a, [(D("0.45"), 100)], now=at), book(b, [(D("0.45"), 100)], now=at)]
    r = A.evaluate_pair(a, b, books, ml_space(), at)
    assert codes(r) == [A.FEE_SCHEDULE_UNKNOWN]
    assert r["economics"] is None


# ═════════════════════════════════════════════════════════════════════
# MAXIMUM PROFITABLE SIZE
# ═════════════════════════════════════════════════════════════════════

def _k_pair():
    a = yes_lal(A.KALSHI, "K1")
    b = yes_bos(A.KALSHI, "K2")
    return a, b


def test_optimum_is_strictly_inside_the_depth():
    # Leg A: 10 @0.45 then 50 @0.52; leg B: 60 @0.45 (both Kalshi), slip 0.01.
    #   Q=10: 4.50 + 4.50 + 0.20 slip + ceil(0.17325)=0.18 + 0.18      = 9.56
    #         net 10 - 9.56                                            = 0.44
    #   Q=11: A 4.50 + 0.52, fee per level 0.18 + ceil(0.017472)=0.02;
    #         B 4.95, fee ceil(0.190575)=0.20; slip 0.22               = 10.59
    #         net 11 - 10.59 = 0.41  -> the 0.52 level loses 0.03 at the margin
    a, b = _k_pair()
    r = A.evaluate_pair(a, b, [book(a, [(D("0.45"), 10), (D("0.52"), 50)]),
                               book(b, [(D("0.45"), 60)])], ml_space(), NOW)
    e = r["economics"]
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS
    assert e["qty"] == 10 and e["depth_by_leg"] == [60, 60]
    assert e["worst_case_net_profit"] == "0.44"
    assert e["marginal_profit_next_contract"] == "-0.03"
    assert e["legs"][0]["levels_consumed"] == [["0.45", 10]]


def test_unsorted_ladder_is_walked_best_first():
    a, b = _k_pair()
    r = A.evaluate_pair(a, b, [book(a, [(D("0.52"), 50), (D("0.45"), 10)]),
                               book(b, [(D("0.45"), 60)])], ml_space(), NOW)
    assert r["economics"]["qty"] == 10


def test_per_order_rounding_decides_whether_and_how_much():
    # Kalshi YES @0.05 + Kalshi YES @0.94 = 0.99: one cent of edge, no slippage.
    #   Q=1:   fees ceil(0.003325)=0.01 + ceil(0.003948)=0.01 -> net -0.01
    #   Q=96:  fees ceil(0.3192)=0.32 + ceil(0.379008)=0.38 -> 0.96-0.70 = 0.26
    #   (Q=99 and Q=100 also net 0.26; the smallest size is kept on a tie.)
    #   Rounded PER CONTRACT the fee would be 0.02 x Q > 0.01 x Q: never.
    a, b = _k_pair()
    sp = ml_space()

    def run(depth):
        return A.evaluate_pair(a, b, [book(a, [(D("0.05"), depth)]),
                                      book(b, [(D("0.94"), depth)])], sp, NOW,
                               slippage_per_contract=0)
    r1 = run(1)
    assert codes(r1) == [A.NOT_PROFITABLE_AFTER_COSTS]
    assert r1["economics"]["worst_case_net_profit"] == "-0.01"
    r = run(100)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS
    e = r["economics"]
    assert e["qty"] == 96 and e["worst_case_net_profit"] == "0.26"
    assert [lg["fee"] for lg in e["legs"]] == ["0.32", "0.38"]
    per_contract_model = 100 * (A.kalshi_taker_fee(1, D("0.05"))
                                + A.kalshi_taker_fee(1, D("0.94")))
    assert D(100) - D(99) - per_contract_model < 0


def test_max_qty_caps_and_fixed_cost_per_order_is_charged_once_per_leg():
    a, b, books = positive_inputs()
    r = A.evaluate_pair(a, b, books, ml_space(), NOW, max_qty=10)
    assert r["economics"]["qty"] == 10
    # fixed 2.00 per order per leg: 4.54 - 4.00 = 0.54 at Q=100
    r = A.evaluate_pair(a, b, books, ml_space(), NOW,
                        fixed_cost_per_order=D("2"))
    assert r["economics"]["worst_case_net_profit"] == "0.54"
    assert [lg["fixed_cost"] for lg in r["economics"]["legs"]] == ["2", "2"]
    r = A.evaluate_pair(a, b, books, ml_space(), NOW, max_scan_qty=7)
    assert r["economics"]["qty"] == 7 and r["economics"]["scan_capped"] is True


def test_not_profitable_after_costs():
    # 0.48 + 0.49 + 0.02 slippage = 0.99, fees ~0.035 / contract -> < 0
    a, b = yes_lal(), yes_bos()
    r = A.evaluate_pair(a, b, [book(a, [(D("0.48"), 50)]),
                               book(b, [(D("0.49"), 50)])], ml_space(), NOW)
    assert codes(r) == [A.NOT_PROFITABLE_AFTER_COSTS]
    assert r["verdict"] == A.REFUSED and r["prices_compared"] is True


# ═════════════════════════════════════════════════════════════════════
# MIDDLES (GENERAL STRUCTURES, FLOOR = MIN PAYOUT)
# ═════════════════════════════════════════════════════════════════════

TEK = "NBA-TOTAL-20261005"


def tspec(subject, line, **kw):
    return spec(subject, ek=TEK, family=A.TOTAL, line=line, **kw)


def middle_legs(lo=D("210.5"), hi=D("211.5"), *, gap=False):
    sp = A.line_buckets(TEK, (lo, hi))
    if not gap:   # OVER lo + UNDER hi (UNDER bought as NO of OVER hi)
        over = A.over_under_payoff(sp, "OVER", lo, void_payout=HALF,
                                   postponed_payout=HALF)
        under = A.over_under_payoff(sp, "UNDER", hi, void_payout=HALF,
                                    postponed_payout=HALF)
        o = A.Contract(A.KALSHI, "TOT-O%s" % lo, A.YES, tspec("OVER", lo), over)
        u = A.Contract(A.KALSHI, "TOT-O%s" % hi, A.NO, tspec("OVER", hi), under)
    else:         # OVER hi + UNDER lo: a GAP, the middle bucket pays nothing
        over = A.over_under_payoff(sp, "OVER", hi, void_payout=HALF,
                                   postponed_payout=HALF)
        under = A.over_under_payoff(sp, "UNDER", lo, void_payout=HALF,
                                    postponed_payout=HALF)
        o = A.Contract(A.KALSHI, "TOT-O%s" % hi, A.YES, tspec("OVER", hi), over)
        u = A.Contract(A.KALSHI, "TOT-O%s" % lo, A.NO, tspec("OVER", lo), under)
    return sp, o, u


def test_line_buckets_partition_by_construction():
    sp = A.line_buckets(TEK, (D("210.5"), D("211.5")))
    assert sp.outcomes == ("LE_210", "EQ_211", "GE_212", "VOID", "POSTPONED")
    assert sp.exhaustive is True and sp.tie_outcomes == ()
    sp = A.line_buckets(TEK, ("-3.5", "4.5", "-3.5"))
    assert sp.regular_outcomes == ("LE_-4", "B_-3_4", "GE_5")


def test_whole_number_lines_need_a_declared_push_rule():
    with pytest.raises(A.RefusedError) as e:
        A.line_buckets(TEK, (D("210.5"), D("211")))
    assert e.value.code == A.TIE_RULE_UNKNOWN
    sp = A.line_buckets(TEK, (D("210.5"), D("211")), push_rule_declared=True)
    assert sp.outcomes == ("LE_210", "EQ_211", "GE_212", "VOID", "POSTPONED")
    assert sp.tie_outcomes == ("EQ_211",)
    with pytest.raises(A.RefusedError) as e:
        A.over_under_payoff(sp, "UNDER", D("211"), void_payout=HALF,
                            postponed_payout=HALF)
    assert e.value.code == A.TIE_RULE_UNKNOWN
    pay = A.over_under_payoff(sp, "UNDER", D("211"), void_payout=HALF,
                              postponed_payout=HALF, push_payout=HALF)
    assert pay["EQ_211"] == HALF and pay["LE_210"] == 1 and pay["GE_212"] == 0
    with pytest.raises(A.RefusedError) as e:      # a line the space never cut
        A.over_under_payoff(sp, "OVER", D("215.5"), void_payout=HALF,
                            postponed_payout=HALF)
    assert e.value.code == A.OUTCOME_SPACE_DIFFERS


def test_middle_floor_is_guaranteed_on_its_floor_not_its_middle():
    # Kalshi OVER 210.5 YES @0.47 + OVER 211.5 NO (= UNDER 211.5) @0.46, x100.
    #   payout: LE_210 1, EQ_211 2, GE_212 1, VOID 0.5+0.5, POSTPONED 1 -> floor 1
    #   fees: ceil(0.07 x 100 x 0.2491 = 1.7437) = 1.75,
    #         ceil(0.07 x 100 x 0.2484 = 1.7388) = 1.74
    #   cost = 47 + 46 + 2.00 + 1.75 + 1.74 = 98.49; floor x 100 = 100 -> 1.51
    sp, o, u = middle_legs()
    books = [book(o, [(D("0.47"), 100)]), book(u, [(D("0.46"), 100)])]
    r = A.evaluate_structure([o, u], books, sp, NOW)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS, codes(r)
    assert r["structure_kind"] == A.MIDDLE_FLOOR
    e = r["economics"]
    assert e["payout_by_outcome"]["EQ_211"] == "2"
    assert e["floor_payout_per_set"] == "1"
    assert e["worst_case_net_profit"] == "1.51" and e["qty"] == 100
    # demanding an exact complement refuses it: the middle bucket sums to 2
    r = A.evaluate_pair(o, u, books, sp, NOW, expect_kind=A.COMPLEMENT)
    assert codes(r) == [A.PAYOFF_NOT_COMPLEMENTARY]


def test_middle_mutation_widening_into_a_gap_refuses():
    sp, o, u = middle_legs(gap=True)
    books = [book(o, [(D("0.47"), 100)]), book(u, [(D("0.46"), 100)])]
    r = A.evaluate_structure([o, u], books, sp, NOW)
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]
    assert r["payoff_table"]["floor_outcomes"] == ["EQ_211"]
    assert r["payoff_table"]["payout_by_outcome"]["EQ_211"] == "0"


def test_middle_mutation_floor_above_one_but_below_raw_cost():
    # A middle bought at 0.55 + 0.52 = 1.07 > floor 1: a bet on the middle.
    sp, o, u = middle_legs()
    books = [book(o, [(D("0.55"), 100)]), book(u, [(D("0.52"), 100)])]
    r = A.evaluate_structure([o, u], books, sp, NOW)
    assert codes(r) == [A.PAYOFF_FLOOR_BELOW_COST]


def test_middle_with_a_declared_push_bucket():
    # OVER 210.5 + UNDER 211 (push pays 0.5): EQ_211 pays 1 + 0.5 = 1.5,
    # floor 1, priced like the half-point middle.
    sp = A.line_buckets(TEK, (D("210.5"), D("211")), push_rule_declared=True)
    pr = "PUSH_PAYS_HALF"
    over = A.over_under_payoff(sp, "OVER", D("210.5"), void_payout=HALF,
                               postponed_payout=HALF)
    under = A.over_under_payoff(sp, "UNDER", D("211"), void_payout=HALF,
                                postponed_payout=HALF, push_payout=HALF)
    o = A.Contract(A.KALSHI, "O210.5", A.YES, tspec("OVER", D("210.5"), tie=pr), over)
    u = A.Contract(A.KALSHI, "O211", A.NO, tspec("OVER", D("211"), tie=pr), under)
    books = [book(o, [(D("0.47"), 100)]), book(u, [(D("0.46"), 100)])]
    r = A.evaluate_structure([o, u], books, sp, NOW)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS
    assert r["economics"]["payout_by_outcome"]["EQ_211"] == "1.5"


# ═════════════════════════════════════════════════════════════════════
# BASKETS
# ═════════════════════════════════════════════════════════════════════

FEK = "NBA-CHAMPION-2027"
TEAMS = ("BOS", "OKC", "DEN", "FIELD")
Q4 = D("0.25")


def fspace(**kw):
    base = dict(event_key=FEK, outcomes=TEAMS + ("VOID", "POSTPONED"),
                exhaustive=True, basis="venue lists BOS/OKC/DEN and FIELD")
    base.update(kw)
    return A.OutcomeSpace(**base)


def fut(team, venue=A.KALSHI, side=A.YES, void=Q4, **kw):
    pay = {t: (1 if t == team else 0) for t in TEAMS}
    if side == A.NO:
        pay = {t: 1 - v for t, v in pay.items()}
    pay.update(VOID=void, POSTPONED=void)
    sp = spec(team, ek=FEK, family=A.FUTURE, period="SEASON",
              void="VOID_PAYS_ONE_OVER_N", **kw)
    return A.Contract(venue, "%s-%s-%s" % (venue, team, side), side, sp, pay,
                      sport="BASKETBALL")


def yes_basket_inputs():
    k = {t: fut(t) for t in TEAMS}
    p_okc = fut("OKC", venue=A.POLYMARKET_US)
    asks = {"BOS": "0.20", "OKC": "0.23", "DEN": "0.24", "FIELD": "0.25"}
    books = [book(k[t], [(D(asks[t]), 50)], 2) for t in TEAMS]
    books.append(book(p_okc, [(D("0.21"), 50)], 1))
    return list(k.values()) + [p_okc], books


def test_yes_basket_mixed_venue_cheapest_and_hand_computed():
    # One YES per outcome, Q=50. OKC is cheaper on Polymarket US (0.21 vs
    # Kalshi 0.23) and is taken there.
    #   notional 50 x (0.20 + 0.21 + 0.24 + 0.25)                 = 45.00
    #   slippage 4 legs x 50 x 0.01                              =  2.00
    #   Kalshi BOS ceil(0.07 x 50 x 0.16   = 0.56)                =  0.56
    #   PMUS   OKC bankers(0.0695 x 50 x 0.1659 = 0.5765025)      =  0.58
    #   Kalshi DEN ceil(0.07 x 50 x 0.1824 = 0.6384)              =  0.64
    #   Kalshi FIELD ceil(0.07 x 50 x 0.1875 = 0.65625)           =  0.66
    #   cost 49.44; payout 1 in every outcome (VOID 4 x 0.25)    -> 0.56
    cands, books = yes_basket_inputs()
    r = A.basket_solver(cands, books, fspace(), NOW)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS, codes(r)
    assert r["structure_kind"] == A.YES_BASKET
    e = r["economics"]
    assert e["qty"] == 50 and e["total_cost"] == "49.44"
    assert e["worst_case_net_profit"] == "0.56"
    okc = [lg for lg in e["legs"] if "OKC" in lg["market_id"]][0]
    assert okc["venue"] == A.POLYMARKET_US and okc["fee"] == "0.58"
    assert set(e["payout_by_outcome"].values()) == {"1", "1.00"}


def test_no_basket_pays_n_minus_one():
    # NO on each of 4: pays 3 in every outcome (VOID 4 x 0.75 = 3).
    #   asks 0.74 + 0.72 + 0.70 + 0.68 = 2.84; Q = 50
    #   fees ceil(3.5 x 0.1924=0.6734)=0.68, ceil(3.5 x 0.2016=0.7056)=0.71,
    #        ceil(3.5 x 0.21=0.735)=0.74, ceil(3.5 x 0.2176=0.7616)=0.77 = 2.90
    #   cost 142 + 2.00 + 2.90 = 146.90; payout 150 -> 3.10
    asks = {"BOS": "0.74", "OKC": "0.72", "DEN": "0.70", "FIELD": "0.68"}
    cands = [fut(t, side=A.NO, void=D("0.75")) for t in TEAMS]
    books = [book(c, [(D(asks[t]), 50)]) for c, t in zip(cands, TEAMS)]
    r = A.basket_solver(cands, books, fspace(), NOW, side=A.NO)
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS, codes(r)
    assert r["structure_kind"] == A.NO_BASKET
    e = r["economics"]
    assert e["floor_payout_per_set"] == "3"
    assert e["total_cost"] == "146.90" and e["worst_case_net_profit"] == "3.10"


def test_basket_refusals():
    cands, books = yes_basket_inputs()
    r = A.basket_solver(cands, books, fspace(exhaustive=False), NOW)
    assert codes(r) == [A.OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE]
    # FIELD listed by the venue but no candidate for it
    r = A.basket_solver([c for c in cands if "FIELD" not in c.market_id],
                        books, fspace(), NOW)
    assert codes(r) == [A.OUTCOME_NOT_COVERED]
    # FIELD omitted from the declared universe: the contracts pay on an
    # outcome the space does not list
    sp = fspace(outcomes=("BOS", "OKC", "DEN", "VOID", "POSTPONED"))
    assert A.OUTCOME_SPACE_DIFFERS in codes(A.basket_solver(cands, books, sp, NOW))
    # a duplicated outcome in the universe
    sp = fspace(outcomes=TEAMS + ("BOS", "VOID", "POSTPONED"))
    assert A.DUPLICATE_OUTCOME in codes(A.basket_solver(cands, books, sp, NOW))
    # the same outcome bought twice as two legs
    r = A.evaluate_structure([cands[0], fut("BOS", venue=A.POLYMARKET_US)]
                             + cands[1:4], books, fspace(), NOW,
                             expect_kind=A.YES_BASKET)
    assert A.DUPLICATE_OUTCOME in codes(r)
    # a leg that can void differently from the others
    odd = fut("DEN")
    odd = replace(odd, spec=replace(odd.spec, void_rule="VOID_REFUNDS"))
    r = A.basket_solver([cands[0], cands[4], odd, cands[3]], books, fspace(), NOW)
    assert A.VOID_RULE_DIFFERS in codes(r)
    # same void rule text but VOID pays 0 on one leg: basket pays 0.75 in VOID
    z = replace(fut("DEN"), payoff=dict(fut("DEN").payoff, VOID=0))
    r = A.basket_solver([cands[0], cands[4], z, cands[3]], books, fspace(), NOW)
    assert codes(r) == [A.BASKET_PAYOFF_NOT_CONSTANT]
    # a candidate that pays on two outcomes is not an indicator
    two = replace(fut("DEN"), payoff=dict(fut("DEN").payoff, FIELD=1))
    r = A.basket_solver([cands[0], cands[4], two, cands[3]], books, fspace(), NOW)
    # ... and DEN is then uncovered, which is reported too
    assert codes(r) == [A.PAYOFF_NOT_INDICATOR, A.OUTCOME_NOT_COVERED]
    # a NO in a YES basket
    r = A.basket_solver([fut("BOS", side=A.NO)] + cands[1:], books, fspace(), NOW)
    assert A.SIDE_INVALID in codes(r)


def test_basket_stale_alternative_is_excluded_not_silently_dropped():
    cands, books = yes_basket_inputs()
    p_okc = cands[-1]
    books = [b for b in books if b.key != p_okc.key] + \
        [book(p_okc, [(D("0.21"), 50)], 31)]
    r = A.basket_solver(cands, books, fspace(), NOW)
    # Kalshi OKC @0.23 is used instead (fee ceil(0.07 x 50 x 0.1771=0.61985)
    # = 0.62): cost 46.00 + 2.00 + 0.56 + 0.62 + 0.64 + 0.66 = 50.48 -> -0.48
    assert codes(r) == [A.NOT_PROFITABLE_AFTER_COSTS]
    ex = r["excluded_alternatives"]
    assert len(ex) == 1 and ex[0]["reasons"][0]["code"] == A.STALE_BOOK


def test_yes_basket_of_two_outcomes_keeps_its_name():
    sp = A.OutcomeSpace(EK, ("LAL", "BOS", "VOID", "POSTPONED"), exhaustive=True)
    a, b, books = positive_inputs()
    r = A.basket_solver([a, b], books, sp, NOW)
    assert r["structure_kind"] == A.YES_BASKET
    assert r["verdict"] == A.GUARANTEED_AFTER_COSTS


# ═════════════════════════════════════════════════════════════════════
# PAIR SCANNER CENSUS
# ═════════════════════════════════════════════════════════════════════

def test_pair_scanner_yields_exactly_one_record_per_pair():
    k_lal, p_bos = yes_lal(), yes_bos()
    p_lal = yes_lal(A.POLYMARKET_US, "P-LAL")
    x_bos = replace(yes_bos(A.POLYMARKET, "X-BOS"), sport=None)
    k_no = A.opposite_side(k_lal)                     # same market as k_lal
    no_key = replace(yes_lal(market="K-ORPHAN"),
                     spec=replace(yes_lal().spec, event_key=""))
    other = yes_lal(market="K-OTHER-EVT", ek="NHL-X")
    other2 = yes_bos(market="P-OTHER-EVT", ek="NHL-X")
    cs = [k_lal, p_bos, p_lal, x_bos, k_no, no_key, other, other2]
    books = [book(c, [(D("0.45"), 100)], 1) for c in cs]
    opps, refs, census = A.pair_scanner(cs, books, NOW,
                                        outcome_spaces={EK: ml_space()})
    # EK has 5 contracts: C(5,2) = 10 pairs, 1 same-market (k_lal / k_no) -> 9
    # NHL-X has 2 contracts and no declared space -> 1
    assert census["pairs_considered"] == len(opps) + len(refs) == 10
    assert census["same_market_pairs_not_considered"] == 1
    assert census["contracts_without_event_key"] == 1
    assert sum(census["by_verdict"].values()) == 10
    assert sum(census["by_primary_refusal_code"].values()) == len(refs)
    seen = [frozenset(tuple(lg[0].values()) for lg in r["inputs"]["legs"])
            for r in opps + refs]
    assert len(seen) == len(set(seen)) == 10
    # k_lal + p_bos and p_lal + p_bos are complements with known fees;
    # k_no + p_lal is also one (NO LAL pays 1 - YES LAL).
    assert len(opps) == census["by_verdict"][A.GUARANTEED_AFTER_COSTS] == 3
    assert {r["structure_kind"] for r in opps} == {A.COMPLEMENT}
    # X-BOS (no fee schedule) is refused on FEES only where the payoff floor
    # passed (with K-LAL and P-LAL); paired with P-BOS / K-LAL-NO the floor is
    # 0 and the prices -- and so the fees -- are never looked at.
    # Floor-0 pairs: K-LAL+P-LAL, P-BOS+X-BOS, P-BOS+K-NO, X-BOS+K-NO.
    assert census["by_refusal_code"] == {
        A.FEE_SCHEDULE_UNKNOWN: 2, A.PAYOFF_FLOOR_BELOW_COST: 4,
        A.OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE: 1}


# ═════════════════════════════════════════════════════════════════════
# MUTATION TESTS ON THE KNOWN GUARANTEED CASE
# ═════════════════════════════════════════════════════════════════════

def _flip_bos(c):
    return replace(c, payoff=dict(c.payoff, BOS=0))


def _rebook(books, i, **kw):
    out = list(books)
    out[i] = replace(out[i], **kw)
    return out


MUTATIONS = {
    "flip_payoff_entry": (lambda a, b, bk, sp: (a, _flip_bos(b), bk, sp, {}),
                          A.PAYOFF_FLOOR_BELOW_COST),
    "flip_payoff_entry_strict": (
        lambda a, b, bk, sp: (a, _flip_bos(b), bk, sp,
                              {"expect_kind": A.COMPLEMENT}),
        A.PAYOFF_NOT_COMPLEMENTARY),
    "void_payout": (lambda a, b, bk, sp: (
        a, replace(b, payoff=dict(b.payoff, VOID=0)), bk, sp, {}),
        A.PAYOFF_FLOOR_BELOW_COST),
    "resolution_source": (lambda a, b, bk, sp: (
        a, replace(b, spec=replace(b.spec, resolution_source="ESPN")), bk, sp,
        {}), A.SETTLEMENT_SOURCE_DIFFERS),
    "void_rule": (lambda a, b, bk, sp: (
        a, replace(b, spec=replace(b.spec, void_rule="VOID_REFUNDS")), bk, sp,
        {}), A.VOID_RULE_DIFFERS),
    "tie_rule": (lambda a, b, bk, sp: (
        a, replace(b, spec=replace(b.spec, tie_rule="TIE_RESOLVES_NO")), bk, sp,
        {}), A.TIE_RULE_DIFFERS),
    "settle_window": (lambda a, b, bk, sp: (
        a, replace(b, spec=replace(b.spec, settle_window=(
            W[0], "2026-10-08T00:00:00+00:00"))), bk, sp, {}),
        A.SETTLE_WINDOW_DIFFERS),
    "event_key": (lambda a, b, bk, sp: (
        a, replace(b, spec=replace(b.spec, event_key="NBA-LAL-BOS-20261006")),
        bk, sp, {}), A.EVENT_IDENTITY_UNPROVEN),
    "age_past_max": (lambda a, b, bk, sp: (
        a, b, _rebook(bk, 0, observed_at=NOW - timedelta(seconds=31)), sp, {}),
        A.STALE_BOOK),
    "skew_past_max": (lambda a, b, bk, sp: (
        a, b, _rebook(bk, 0, observed_at=NOW - timedelta(seconds=7)), sp, {}),
        A.UNSYNCHRONIZED_BOOKS),
    "remove_depth": (lambda a, b, bk, sp: (a, b, _rebook(bk, 1, asks=()), sp, {}),
                     A.NO_EXECUTABLE_DEPTH),
    "drop_fee_schedule": (lambda a, b, bk, sp: (
        a, replace(b, sport=None), bk, sp, {}), A.FEE_SCHEDULE_UNKNOWN),
    "venue_without_schedule": (lambda a, b, bk, sp: (
        a, replace(b, venue=A.POLYMARKET), _rebook(bk, 1, venue=A.POLYMARKET),
        sp, {}), A.FEE_SCHEDULE_UNKNOWN),
    "space_not_exhaustive": (lambda a, b, bk, sp: (
        a, b, bk, replace(sp, exhaustive=False), {}),
        A.OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE),
    "space_without_void": (lambda a, b, bk, sp: (
        a, b, bk, replace(sp, outcomes=("LAL", "BOS", "POSTPONED")), {}),
        A.NONSTANDARD_OUTCOMES_MISSING),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_of_the_guaranteed_case_refuses(name):
    a, b, books = positive_inputs()
    sp = ml_space()
    assert A.evaluate_pair(a, b, books, sp, NOW)["verdict"] == \
        A.GUARANTEED_AFTER_COSTS
    mutate, code = MUTATIONS[name]
    ma, mb, mbk, msp, kw = mutate(a, b, books, sp)
    r = A.evaluate_pair(ma, mb, mbk, msp, NOW, **kw)
    assert r["verdict"] == A.REFUSED, name
    assert code in codes(r), (name, codes(r))


def test_mutation_one_cent_past_breakeven():
    # leg B @0.49: cost 45 + 49 + 2.00 + 1.74 + ceil-free PMUS
    #   bankers(0.0695 x 100 x 0.2499 = 1.736805) = 1.74 -> 99.48 -> +0.52
    # leg B @0.50: PMUS bankers(1.7375) = 1.74 -> 100.48 -> -0.48, and every
    #   smaller size is negative too (Q=1: 0.95 + 0.02 + 0.02 + 0.02 = 1.01)
    a, b, books = positive_inputs()
    ok = A.evaluate_pair(a, b, _rebook(books, 1, asks=((D("0.49"), 100),)),
                         ml_space(), NOW)
    assert ok["verdict"] == A.GUARANTEED_AFTER_COSTS
    assert ok["economics"]["worst_case_net_profit"] == "0.52"
    bad = A.evaluate_pair(a, b, _rebook(books, 1, asks=((D("0.50"), 100),)),
                          ml_space(), NOW)
    assert codes(bad) == [A.NOT_PROFITABLE_AFTER_COSTS]


def test_a_zero_fee_mutant_engine_is_caught(monkeypatch):
    """If fees were silently zero, the positive-case pins and the breakeven
    test would fail: the suite detects the mutant."""
    check_positive_case()
    monkeypatch.setattr(
        A, "order_fee",
        lambda venue, fills, *, at, sport=None: A.FeeQuote(True, D(0), None,
                                                           "MUTANT_ZERO"))
    with pytest.raises(AssertionError):
        check_positive_case()
    with pytest.raises(AssertionError):
        test_mutation_one_cent_past_breakeven()
    with pytest.raises(AssertionError):
        test_unknown_fee_schedule_refuses_never_zero(A.POLYMARKET, "X", NOW)


def test_a_per_contract_rounding_mutant_is_caught(monkeypatch):
    real = A.kalshi_taker_fee
    monkeypatch.setattr(A, "kalshi_taker_fee",
                        lambda c, p: real(1, p) * int(c))
    with pytest.raises(AssertionError):
        test_per_order_rounding_decides_whether_and_how_much()


# ═════════════════════════════════════════════════════════════════════
# LEG-RISK STATE MACHINE
# ═════════════════════════════════════════════════════════════════════

def _planned():
    a, b = yes_lal(), yes_bos()
    # Kalshi thinner (60) than PMUS (100): Kalshi goes first
    r = A.evaluate_pair(a, b, [book(a, [(D("0.45"), 60)]),
                               book(b, [(D("0.45"), 100)])], ml_space(), NOW)
    return A.plan_execution(r)


def _step(ex, *events):
    for ev in events:
        t = A.transition(ex, ev)
        assert t.ok, t.refusal
        ex = t.execution
    return ex


def test_leg_order_rule_thinner_then_less_reliable_first():
    ex = _planned()
    assert ex.state == A.PLANNED and ex.target_qty == 60
    assert ex.leg_a.venue == A.KALSHI and ex.leg_b.venue == A.POLYMARKET_US
    x = {"venue": "X", "depth": 10, "reliability": 0.9}
    y = {"venue": "Y", "depth": 10, "reliability": 0.5}
    assert A.choose_leg_order(x, y) == (y, x)
    assert A.choose_leg_order({"venue": "Z", "depth": 5}, x)[0]["venue"] == "Z"


def test_happy_path_to_closed_matched():
    E = A.Event
    ex = _step(_planned(),
               E(A.EV_WORK, "A"),
               E(A.EV_FILL, "A", 60, D("0.45"), D("1.04")),
               E(A.EV_WORK, "B"),
               E(A.EV_FILL, "B", 20, D("0.45"), D("0.35")),
               E(A.EV_FILL, "B", 40, D("0.45"), D("0.69")))
    assert ex.state == A.MATCHED
    x = A.exposure(ex)
    # 60 x (1 - 0.45 - 0.45) - fees 1.04 - 0.35 - 0.69 = 6.00 - 2.08 = 3.92
    assert x["matched_qty"] == 60 and x["unhedged_qty_a"] == 0
    assert x["matched_locked_pnl"] == D("3.92")
    assert x["worst_case_total_pnl"] == D("3.92")
    ex = _step(ex, E(A.EV_CLOSE))
    assert ex.state == A.CLOSED_MATCHED
    states = [h[0] for h in ex.history] + [ex.state]
    assert states == [A.PLANNED, A.LEG_A_WORKING, A.LEG_A_FILLED,
                      A.LEG_B_WORKING, A.LEG_B_PARTIAL, A.MATCHED,
                      A.CLOSED_MATCHED]


def _unhedged():
    E = A.Event
    ex = _step(_planned(),
               E(A.EV_WORK, "A"),
               E(A.EV_FILL, "A", 60, D("0.45"), D("1.08")),
               E(A.EV_WORK, "B"),
               E(A.EV_FILL, "B", 20, D("0.45"), D("0.35")),
               E(A.EV_TIMEOUT, "B"))
    assert ex.state == A.UNHEDGED_EXPOSURE
    return ex


def test_partial_fill_then_unhedged_worst_case_math():
    x = A.exposure(_unhedged())
    # leg A all-in: (60 x 0.45 + 1.08) / 60 = 28.08 / 60 = 0.468
    # unhedged 40 x 0.468 = 18.72 worst case (it settles at 0)
    # matched 20 x (1 - 0.468 - (9.00 + 0.35)/20 = 0.4675) = 20 x 0.0645 = 1.29
    assert x["matched_qty"] == 20 and x["unhedged_qty_a"] == 40
    assert x["worst_case_loss_unhedged"] == D("18.72")
    assert x["matched_locked_pnl"] == D("1.29")
    assert x["worst_case_total_pnl"] == D("1.29") - D("18.72")


def test_recovery_complete_leg():
    E = A.Event
    ex = _unhedged()
    rec = A.recommend_recovery(ex, complete_ask=D("0.47"), complete_fee=D("0.70"),
                               unwind_bid=D("0.40"), unwind_fee=D("0.68"))
    # complete: 40 - 18.72 - 18.80 - 0.70 = 1.78; unwind: 16 - 18.72 - 0.68 = -3.40
    assert rec["complete_pnl"] == D("1.78") and rec["unwind_pnl"] == D("-3.40")
    assert rec["choice"] == A.EV_RECOVER_COMPLETE
    ex = _step(ex, E(A.EV_RECOVER_COMPLETE), E(A.EV_FILL, "B", 15, D("0.47"), 0))
    assert ex.state == A.RECOVERY_COMPLETE_LEG
    t = A.transition(ex, E(A.EV_FILL, "B", 26, D("0.47"), 0))
    assert not t.ok and t.refusal["code"] == A.OVERFILL
    ex = _step(ex, E(A.EV_FILL, "B", 25, D("0.47"), D("0.70")))
    assert ex.state == A.CLOSED_MATCHED
    assert A.exposure(ex)["unhedged_qty_a"] == 0


def test_recovery_unwind():
    E = A.Event
    ex = _step(_unhedged(), E(A.EV_RECOVER_UNWIND),
               E(A.EV_UNWIND_FILL, "A", 30, D("0.40"), D("0.50")))
    assert ex.state == A.RECOVERY_UNWIND
    ex = _step(ex, E(A.EV_UNWIND_FILL, "A", 10, D("0.40"), D("0.18")))
    assert ex.state == A.CLOSED_UNWOUND
    x = A.exposure(ex)
    # proceeds 40 x 0.40 - 0.68 = 15.32; basis 40 x 0.468 = 18.72 -> -3.40
    assert x["unhedged_qty_a"] == 0 and x["matched_qty"] == 20
    assert x["realized_unwind_pnl"] == D("-3.40")
    assert x["worst_case_total_pnl"] == D("1.29") - D("3.40")


def test_book_moved_past_breakeven_exposes_and_leg_a_short_fill_resizes():
    E = A.Event
    ex = _step(_planned(), E(A.EV_WORK, "A"),
               E(A.EV_FILL, "A", 60, D("0.45"), D("1.08")), E(A.EV_WORK, "B"))
    still = _step(ex, E(A.EV_BOOK_MOVED, "B", price=D("0.53")))
    assert still.state == A.LEG_B_WORKING        # 0.53 <= 1 - 0.468
    gone = _step(ex, E(A.EV_BOOK_MOVED, "B", price=D("0.54")))
    assert gone.state == A.UNHEDGED_EXPOSURE
    ex = _step(_planned(), E(A.EV_WORK, "A"),
               E(A.EV_FILL, "A", 25, D("0.45"), 0), E(A.EV_REJECT, "A"))
    assert ex.state == A.LEG_A_FILLED and ex.target_qty == 25


def test_invalid_transitions_are_refused_and_change_nothing():
    E = A.Event
    ex = _planned()
    for ev in (E(A.EV_FILL, "B", 1, D("0.45")), E(A.EV_WORK, "B"),
               E(A.EV_CLOSE), E(A.EV_RECOVER_UNWIND), E("TELEPORT"),
               E(A.EV_FILL, "A", 1, D("1.5"))):
        t = A.transition(ex, ev)
        assert not t.ok and t.refusal["code"] == A.INVALID_TRANSITION, ev
        assert t.execution is ex
    t = A.transition(_step(ex, E(A.EV_WORK, "A")),
                     E(A.EV_FILL, "A", 61, D("0.45")))
    assert t.refusal["code"] == A.OVERFILL
    aborted = _step(ex, E(A.EV_WORK, "A"), E(A.EV_TIMEOUT, "A"))
    assert aborted.state == A.ABORTED
    assert A.transition(aborted, E(A.EV_WORK, "A")).refusal["code"] == \
        A.INVALID_TRANSITION
    assert _step(ex, E(A.EV_ABORT)).state == A.ABORTED
    with_inv = _step(ex, E(A.EV_WORK, "A"), E(A.EV_FILL, "A", 5, D("0.45")))
    assert A.transition(with_inv, E(A.EV_ABORT)).refusal["code"] == \
        A.INVALID_TRANSITION
    with pytest.raises(A.RefusedError):
        A.plan_execution({"verdict": A.REFUSED})
    with pytest.raises(A.RefusedError):
        A.recommend_recovery(ex, complete_ask=1, complete_fee=0, unwind_bid=0,
                             unwind_fee=0)


# ═════════════════════════════════════════════════════════════════════
# AUTHORITY
# ═════════════════════════════════════════════════════════════════════

FORBIDDEN_MODULE_PARTS = (
    "venue", "kalshi", "clob", "live_executor", "bettor_funded", "submission",
    "execmirror", "bettor_xavier", "workers", "pmus", "pmx", "edge_gate",
    "entry_execution", "execution_gate", "httpx", "requests", "urllib",
    "socket", "aiohttp", "psycopg", "asyncpg", "sqlite", "sqlalchemy", "os",
    "subprocess")


def _imports():
    names = []
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_ast_imports_are_stdlib_plus_the_pure_fee_schedule():
    names = _imports()
    for n in names:
        parts = n.lower().split(".")
        for bad in FORBIDDEN_MODULE_PARTS:
            if bad in ("os", "pmx", "pmus", "clob"):
                assert bad not in parts, n
            else:
                assert bad not in n.lower(), (n, bad)
    allowed = {"__future__", "annotations", "math", "dataclasses", "dataclass",
               "replace", "datetime", "timedelta", "decimal", "Decimal",
               "InvalidOperation", "ROUND_CEILING", "ROUND_FLOOR", "itertools",
               "combinations", "typing", "Any", "Iterable", "Mapping",
               "Sequence", "", "bettor_fee_schedule"}
    assert set(names) <= allowed, set(names) - allowed


def test_forbidden_strings_are_absent():
    low = SRC.lower()
    # "submit" is allowed in exactly one place: the AUTHORITY declaration that
    # says it is False. Everywhere else it is forbidden.
    decl = '    "submit": False,\n'
    assert SRC.count(decl) == 1
    rest = low.replace(decl.lower(), "", 1)
    for bad in ("submit", "cancel_order", "create_order", "chat.postmessage",
                "os.environ", "getenv", "httpx", "requests", "urllib", "socket",
                "insert into", "delete from", "create table", "order_submitted",
                "market_positions(", "api_positions", "priority=true"):
        assert bad not in rest, bad
    assert not re.search(r"\bupdate\s+[a-z_]+\s+set\b", low)


def test_authority_is_declared_false_and_enforced(monkeypatch):
    assert A.AUTHORITY == {"submit": False, "cancel": False,
                           "credentials": False, "capital": False,
                           "mode": "SHADOW"}
    assert A.assert_no_authority() is True
    monkeypatch.setitem(A.AUTHORITY, "capital", True)
    with pytest.raises(AssertionError):
        A.assert_no_authority()
    monkeypatch.setitem(A.AUTHORITY, "capital", False)
    monkeypatch.setitem(A.AUTHORITY, "mode", "LIVE")
    with pytest.raises(AssertionError):
        A.assert_no_authority()


def test_only_two_verdicts_and_guaranteed_is_one_of_them():
    assert A.VERDICTS == ("GUARANTEED_AFTER_COSTS", "REFUSED")
    for name in dir(A):
        v = getattr(A, name)
        if isinstance(v, str) and "GUARANTEED" in v.upper() and name.isupper():
            assert v == A.GUARANTEED_AFTER_COSTS, name
    for code in A.REFUSAL_CODES:
        assert "GUARANTEE" not in code


def test_every_refusal_code_is_exercised_by_this_suite():
    me = pathlib.Path(__file__).read_text()
    body = me.split("def test_every_refusal_code_is_exercised_by_this_suite")[0]
    missing = [c for c in A.REFUSAL_CODES if "A.%s" % c not in body]
    assert missing == [], missing
    assert len(set(A.REFUSAL_CODES)) == len(A.REFUSAL_CODES)
