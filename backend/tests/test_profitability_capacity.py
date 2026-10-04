"""THE CAPACITY MODEL: headline vs executable opportunity from the recorded
book, edge at size, the capacity ceiling -- and UNAVAILABLE (no number at
all) whenever there is no depth evidence.

Pure tests with a flat 1c/contract fee so every figure is hand-checkable:
offers 100 @0.50, 100 @0.55, 1000 @0.60; bids 200 @0.48; p = 0.58 (LONG).
  marginal net edge  0.07, 0.02, -0.03 per contract
  executable         200 contracts, cost 51 + 56 = 107, profit 7 + 2 = 9
  theoretical        (0.58 - 0.50) x 1200 = 96
  ceiling            9 / 0.03 = 300 more contracts at 0.61 -> 107 + 183 = 290
"""
from __future__ import annotations

import pytest

from sportsassets.profitability import capacity as CP
from sportsassets.profitability import runner as RUN

T = 1_790_000_000.0
BOOK = {"obs_id": 7, "observed_at": T - 10,
        "bids": [{"px": {"value": "0.48"}, "qty": "200"}],
        "offers": [{"px": {"value": "0.50"}, "qty": "100"},
                   {"px": {"value": "0.55"}, "qty": "100"},
                   {"px": {"value": "0.60"}, "qty": "1000"}]}


def flat(px):
    return 0.01


def _cand(**kw):
    c = {"candidate_id": "paperdec:c1", "decided_at": T,
         "us_market_slug": "m", "holding_side": "LONG", "probability": 0.58,
         "strategy": "S"}
    c.update(kw)
    return c


def test_theoretical_versus_executable_opportunity():
    r = CP.assess(_cand(), BOOK, fee_fn=flat)
    assert r["status"] == "MEASURED" and r["why"] is None
    assert r["best_price"] == 0.5
    assert r["visible_depth_contracts"] == 1200
    assert r["theoretical_opportunity_dollars"] == pytest.approx(96.0)
    assert r["max_executable_contracts"] == pytest.approx(200.0)
    assert r["executable_capacity_usd"] == pytest.approx(107.0)
    assert r["executable_opportunity_dollars"] == pytest.approx(9.0)
    assert r["executable_opportunity_usd"] == r[
        "executable_opportunity_dollars"]                         # alias
    assert r["expected_price_impact"] == pytest.approx(0.025)
    assert r["capacity_ceiling_usd"] == pytest.approx(290.0)
    assert r["capacity_ceiling_depth_bound"] is False
    assert r["exit_liquidity_usd"] == pytest.approx(96.0)       # 200 @0.48
    assert r["exit_unabsorbed_contracts"] == 0.0


def test_expected_edge_at_size_decays_and_stops_at_the_visible_book():
    r = CP.assess(_cand(), BOOK, fee_fn=flat)
    g = {x["size_usd"]: x for x in r["edge_at_size"]}
    assert g[10.0]["edge_per_dollar"] == pytest.approx(0.07 / 0.51, rel=1e-6)
    assert g[100.0]["contracts"] == pytest.approx(100 + 49 / 0.56)
    assert g[100.0]["expected_net_profit_usd"] == pytest.approx(
        7 + (49 / 0.56) * 0.02)
    q500 = 393 / 0.61
    assert g[500.0]["expected_net_profit_usd"] == pytest.approx(
        9 - q500 * 0.03, rel=1e-6)
    # the whole book costs 717: $1,000 and above are beyond it -- no edge
    for s in (1000.0, 2500.0, 5000.0, 10000.0):
        assert g[s]["status"] == "EXCEEDS_VISIBLE_DEPTH"
        assert g[s]["edge_per_dollar"] is None
    eps = [x["edge_per_dollar"] for x in r["edge_at_size"]
           if x["status"] == "MEASURED"]
    assert eps == sorted(eps, reverse=True)                 # edge decays
    assert r["edge_decay_per_1000_usd"] == pytest.approx(
        (eps[0] - eps[-1]) / ((500 - 10) / 1000.0), rel=1e-6)


def test_a_book_exhausted_before_break_even_is_a_depth_bound_ceiling():
    book = dict(BOOK, offers=[{"px": {"value": "0.50"}, "qty": "100"}])
    r = CP.assess(_cand(), book, fee_fn=flat)
    assert r["capacity_ceiling_usd"] == pytest.approx(51.0)
    assert r["capacity_ceiling_depth_bound"] is True


def test_the_short_side_buys_the_complement_against_the_bids():
    r = CP.assess(_cand(holding_side="SHORT", probability=0.60), BOOK,
                  fee_fn=flat)
    assert r["best_price"] == pytest.approx(0.52)
    assert r["executable_opportunity_dollars"] == pytest.approx(
        200 * (0.60 - 0.52 - 0.01))


def test_no_positive_edge_is_a_measured_zero_with_impact_named():
    r = CP.assess(_cand(probability=0.45), BOOK, fee_fn=flat)
    assert r["status"] == "MEASURED"
    assert r["executable_capacity_usd"] == 0.0
    assert r["theoretical_opportunity_dollars"] == 0.0
    assert r["expected_price_impact"] is None
    assert r["unmeasured"]["expected_price_impact"] == (
        "NO_LEVEL_WITH_POSITIVE_NET_EDGE")


# ── fail-closed ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cand,book,fee,why", [
    (_cand(probability=None), BOOK, flat, CP.R_NO_P),
    (_cand(probability=1.2), BOOK, flat, CP.R_NO_P),
    (_cand(holding_side=None), BOOK, flat, CP.R_NO_SIDE),
    (_cand(), None, flat, CP.R_NO_BOOK),
    (_cand(), dict(BOOK, observed_at=T - 400), flat, CP.R_STALE),
    (_cand(decided_at=None), BOOK, flat, CP.R_STALE),
    (_cand(), dict(BOOK, offers=[]), flat, CP.R_EMPTY),
    (_cand(), dict(BOOK, offers=[{"px": {"value": "abc"}, "qty": "x"}]),
     flat, CP.R_EMPTY),
    (_cand(), BOOK, None, CP.R_NO_FEE),
    (_cand(), BOOK, lambda px: 1 / 0, CP.R_NO_FEE),
])
def test_no_depth_evidence_is_unavailable_never_a_number(cand, book, fee,
                                                         why):
    r = CP.assess(cand, book, fee_fn=fee)
    assert r["status"] == "UNAVAILABLE" and r["why"] == why
    for k in ("theoretical_opportunity_dollars",
              "executable_opportunity_dollars", "executable_capacity_usd",
              "capacity_ceiling_usd", "visible_depth_usd"):
        assert r[k] is None and r["unmeasured"][k] == why
    assert r["edge_at_size"] is None


def test_the_published_fee_schedule_prices_the_levels():
    import datetime as dt

    from sportsassets import bettor_fee_schedule as FS

    fee = RUN.fee_fn_for(T)
    day = dt.datetime.fromtimestamp(T, dt.timezone.utc).date().isoformat()
    assert fee(0.5) == pytest.approx(
        float(FS.for_date(day).theta_taker) * 0.25)
    assert RUN.fee_fn_for(None) is None


# ── aggregate ────────────────────────────────────────────────────────

def test_the_aggregate_counts_unavailable_by_reason_and_never_as_zero():
    a = CP.assess(_cand(candidate_id="c1", decided_at=T - 100,
                        us_market_slug="m1"),
                  dict(BOOK, observed_at=T - 110), fee_fn=flat)
    a2 = CP.assess(_cand(candidate_id="c2", us_market_slug="m1"), BOOK,
                   fee_fn=flat)
    b = CP.assess(_cand(candidate_id="c3", us_market_slug="m2"), None,
                  fee_fn=flat)
    rates = {"fill_probability": {"value": None,
                                  "why": "FEWER_THAN_20_TERMINAL"}}
    agg = CP.aggregate([a, a2, b], rates=rates)
    assert agg["markets"] == 2 and agg["measured_markets"] == 1
    assert agg["unavailable_by_reason"] == {CP.R_NO_BOOK: 1}
    assert agg["EXECUTABLE_OPPORTUNITY_DOLLARS"] == pytest.approx(9.0)
    assert agg["THEORETICAL_OPPORTUNITY_DOLLARS"] == pytest.approx(96.0)
    assert agg["CAPACITY_CEILING_USD"] == pytest.approx(290.0)
    assert agg["EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS"] is None
    assert agg["unmeasured"]["EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS"] == (
        "FEWER_THAN_20_TERMINAL")
    agg = CP.aggregate([a, b], rates={"fill_probability": {"value": 0.5}})
    assert agg["EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS"] == pytest.approx(
        4.5)
    none = CP.aggregate([b], rates=rates)
    assert none["EXECUTABLE_OPPORTUNITY_DOLLARS"] is None
    assert none["unmeasured"]["EXECUTABLE_OPPORTUNITY_DOLLARS"] == (
        "NO_CANDIDATE_WITH_DEPTH_EVIDENCE")


def test_a_rate_below_its_sample_floor_is_unavailable():
    assert CP.rate(5, 10, 3, min_n=20, basis="b", why="FEW")["value"] is None
    assert CP.rate(5, 10, 30, min_n=20, basis="b", why="FEW")["value"] == 0.5
    assert CP.rate(5, 0, 30, min_n=20, basis="b", why="FEW")["why"] == (
        "ZERO_DENOMINATOR")
