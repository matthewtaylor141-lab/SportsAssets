"""§E. THE FIVE CONFUSIONS THAT WOULD MAKE THIS DATASET WORTHLESS.

Owner directive, "GO on the narrow next step" §E:

    "I want an explicit regression test protecting:
     TOUCH != FILL
     TRADE_AT_PRICE != OUR_FILL
     TRADE_THROUGH != ACTUAL_BETTOR_FILL
     DISPLAYED_DEPTH != DYNAMIC_QUEUE
     CUMULATIVE_VOLUME != QUEUE_EVOLUTION
     The current scientific discipline must survive this
     implementation."

Each of these is a pair of quantities that look interchangeable, are
routinely treated as interchangeable, and are not. Every one of them,
if conflated, produces a dataset that reports fills nobody earned --
and the conflation is invisible at the call site, which is why it is
pinned here rather than trusted to review.

These tests assert over the SHIPPED modules, not over fixtures, so a
future change that reintroduces a confusion fails here.
"""

import pytest

from sportsassets import bettor_book_snapshot as bs
from sportsassets import bettor_p_fill as pf
from sportsassets import bettor_shadow_execution as sx


def _snap(bid_qty="120", ask_qty="60"):
    return bs.snapshot({
        "bids": [{"px": {"value": "0.52"}, "qty": bid_qty}],
        "offers": [{"px": {"value": "0.55"}, "qty": ask_qty}],
        "stats": {"sharesTraded": "48210"},
        "state": "MARKET_STATE_OPEN",
    }, symbol="aec-test", captured_at="T0", feed="book")


# ── 1. TOUCH != FILL ─────────────────────────────────────────────────

def test_a_touch_is_not_a_fill_anywhere_in_the_label_set():
    """The price reaching our level is not somebody trading with us."""
    assert "TOUCH" not in sx.LABELS
    for label in sx.LABELS:
        assert "TOUCH" not in label
    assert pf.FORBIDDEN_AS_P_FILL["TOUCH"]
    assert "were never in the queue" in pf.FORBIDDEN_AS_P_FILL["TOUCH"]


def test_the_research_module_still_refuses_to_promote_a_touch():
    from sportsassets import bettor_ev_bridge as evb
    try:
        MF = evb.machinery(None)["maker_fill"]
    except evb.MachineryUnavailable:
        pytest.skip("research machinery not on this path")
    assert MF.TOUCH_IS_NOT_A_FILL_STATUS is True
    assert hasattr(MF, "TouchPromotion")


# ── 2. TRADE_AT_PRICE != OUR_FILL ────────────────────────────────────

def test_volume_at_our_price_never_produces_a_positive_label():
    """Trading occurred at the level. How much of it was ahead of us is
    exactly the unobserved quantity."""
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level="500")
    for vol in ("1", "500", "5000", "5000000"):
        r = sx.label_outcome(q, volume_at_or_through_price=vol)
        assert r["COUNTERFACTUAL_FILL_STATUS"] != sx.FILL_SUPPORTED, vol
        assert r["EVIDENCE_CLASS"] != sx.POSITIVE_SUPPORTED, vol
    assert "not positive fill evidence" in sx.AT_PRICE_VOLUME_IS_NOT_A_FILL


def test_at_price_volume_reaches_censored_and_stops_there():
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level="500")
    r = sx.label_outcome(q, volume_at_or_through_price="9999")
    assert r["EVIDENCE_CLASS"] == sx.INTERVAL_CENSORED


# ── 3. TRADE_THROUGH != ACTUAL_BETTOR_FILL ───────────────────────────

def test_the_strongest_positive_is_still_counterfactual():
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level="500")
    r = sx.label_outcome(q, traded_through=True)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_SUPPORTED
    assert r[sx.ACTUAL] == sx.NOT_IDENTIFIED
    assert sx.ACTUAL_BETTOR_FILL == sx.NOT_IDENTIFIED
    assert sx.ORDER_PATH_EXISTS is False


def test_no_input_at_all_produces_an_actual_fill():
    """Swept over the whole argument surface, not a chosen example."""
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level="500",
        hidden_liquidity_status=sx.HIDDEN_LIQUIDITY_IDENTIFIED,
        priority_semantics=sx.PRIORITY_IDENTIFIED)
    for through in (True, False, None):
        for vol in (None, "0", "999999"):
            r = sx.label_outcome(
                q, traded_through=through, volume_at_or_through_price=vol,
                queue_dynamic_status=sx.QUEUE_DYNAMIC_OBSERVED,
                depletion_from_trades="10",
                depletion_from_cancellations="10",
                addition_ahead="0", effective_queue_ahead_min="1")
            assert r[sx.ACTUAL] == sx.NOT_IDENTIFIED
            assert "FILLED" not in str(r.get("COUNTERFACTUAL_FILL_STATUS"))


def test_the_positive_carries_an_unverified_market_impact_assumption():
    """A real order would have been in the tape it is judged against."""
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level="500")
    joined = " ".join(sx.label_outcome(q, traded_through=True)["assumptions"])
    assert "NO_MARKET_IMPACT" in joined
    assert "NOT VERIFIED" in joined


# ── 4. DISPLAYED_DEPTH != DYNAMIC_QUEUE ──────────────────────────────

def test_the_snapshot_reports_displayed_depth_and_an_unobserved_queue():
    s = _snap()
    assert s["DISPLAYED_DEPTH_AT_T0"]["bid"] == "120"
    assert s["QUEUE_AHEAD_DYNAMIC_STATUS"] == "NOT_OBSERVED"
    assert "observation" in s["displayedDepthIsNotQueueAhead"]
    assert "counterfactual" in s["displayedDepthIsNotQueueAhead"]


def test_queue_ahead_never_arrives_without_its_assumptions():
    q = bs.queue_ahead_at_t0(_snap(), side="BID", price="0.52")
    assert q["QUEUE_AHEAD_AT_T0"] == "120"
    names = {a["name"] for a in q["assumptions"]}
    assert names == {"JOINS_THE_BACK_OF_THE_QUEUE",
                     "DISPLAYED_IS_ALL_THERE_IS", "NO_RACE",
                     "SNAPSHOT_IS_THE_INSERTION_INSTANT"}
    assert "assumed, not observed" in q["assumptionsAreAssumed"]


def test_one_snapshot_never_reports_queue_evolution():
    q = bs.queue_ahead_at_t0(_snap(), side="BID", price="0.52")
    assert q["QUEUE_AHEAD_DYNAMIC_STATUS"] == "NOT_OBSERVED"
    assert "properties of a sequence" in q["noEvolutionFromOneSnapshot"]
    for forbidden in ("QUEUE_DEPLETION_FROM_CANCELLATIONS",
                      "QUEUE_DEPLETION_FROM_TRADES",
                      "QUEUE_ADDITION_AHEAD"):
        assert forbidden not in q, forbidden


def test_a_price_the_venue_is_not_showing_is_not_a_queue_of_zero():
    """THE DEFAULT THAT WOULD MANUFACTURE FILLS. A zero queue means we
    would be first in line."""
    q = bs.queue_ahead_at_t0(_snap(), side="BID", price="0.40")
    assert q["QUEUE_AHEAD_AT_T0"] == bs.NOT_IDENTIFIED
    assert q["QUEUE_AHEAD_AT_T0"] != "0"
    assert q["DERIVATION"] == bs.QUEUE_AHEAD_NOT_DERIVABLE


def test_the_only_measured_zero_is_a_price_improvement():
    q = bs.queue_ahead_at_t0(_snap(), side="BID", price="0.53")
    assert q["QUEUE_AHEAD_AT_T0"] == "0"
    assert q["DERIVATION"] == bs.QUEUE_AHEAD_PRICE_IMPROVEMENT
    assert "IS the measurement" in q["whyZeroIsMeasuredHere"]


def test_an_unparseable_quantity_is_not_summed_into_a_total():
    """A partial sum presented as a total is a smaller number that
    looks like a measurement."""
    s = bs.snapshot({
        "bids": [{"px": {"value": "0.52"}, "qty": "120"},
                 {"px": {"value": "0.51"}, "qty": "n/a"}],
        "offers": [], "stats": {},
    }, symbol="x", captured_at="T0")
    assert s["DISPLAYED_DEPTH_AT_T0"]["bid"] == bs.NOT_IDENTIFIED
    assert bs.R_QTY_UNPARSEABLE in s["MISSING_FIELD_REASONS"]


# ── 5. CUMULATIVE_VOLUME != QUEUE_EVOLUTION ──────────────────────────

def test_shares_traded_is_labelled_cumulative_and_market_wide():
    s = _snap()
    assert s["STATS_SHARES_TRADED"] == "48210"
    note = s["sharesTradedIsCumulative"]
    assert "running total" in note
    assert "not queue evolution" in note
    assert "never volume at our price" in note


def test_the_snapshot_publishes_no_queue_evolution_field():
    s = _snap()
    for forbidden in ("QUEUE_DEPLETION_FROM_TRADES",
                      "QUEUE_DEPLETION_FROM_CANCELLATIONS",
                      "QUEUE_ADDITION_AHEAD"):
        assert forbidden not in s, forbidden


def test_the_retracted_snapshot_bound_claim_stays_retracted():
    r = sx.RETRACTED_CLAIMS["QUEUE_AHEAD_IS_A_LOWER_BOUND"]
    assert r["status"] == "RETRACTED"
    assert "NOT a permanent lower bound" in sx.QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND


# ── the read path did not become the mirror's read path ──────────────

def test_bbo_read_is_not_modified_by_this_work():
    """The mirror lane depends on its exact four-key return and the
    mirror is frozen."""
    import inspect
    from sportsassets import pmus
    src = inspect.getsource(pmus.bbo_read)
    assert '"bid": None, "ask": None, "state": None, "error": None' in src
    assert "qty" not in src
    assert "sharesTraded" not in src
    assert "frozen" in bs.describe()["bboReadIsNotTouched"]
