"""THE MANAGEMENT VALUATION, and the six ways it could flatter itself.

Every assertion below pins a property that, if it broke, would produce a
plausible-looking comparison table that recommends the wrong action. The
controls matter as much as the positive cases: a refusal test passes
vacuously if the thing never scores at all.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_mgmt_value as MV            # noqa: E402


def flat_fee(rate=0.01):
    return lambda qty, price: round(rate * qty * price, 6)


def pos(qty=100.0, basis=45.0):
    return MV.Position(condition_id="0xc1", outcome_index=0,
                       qty=qty, basis_usd=basis)


# ── HOLD is priced, and priced differently depending on the evidence ──

def test_hold_is_exact_when_the_market_has_settled():
    """The historical case. payout is a FACT, so hold needs no model."""
    mk = MV.Market(payout=1.0, payout_source=MV.SETTLED_FACT)
    c = MV.value_hold(pos(100.0, 45.0), mk)
    assert c.status == MV.IDENTIFIED
    assert abs(c.ev_usd - 55.0) < 1e-9          # 1.00*100 - 45
    assert c.assumptions == [], c.assumptions   # nothing is assumed
    assert "settled" in c.uncertainty


def test_hold_is_refused_when_nothing_says_what_settlement_pays():
    """The live case, and the reason the live test cannot be first."""
    c = MV.value_hold(pos(), MV.Market(bid=0.40, bid_size=500))
    assert c.status == MV.NOT_IDENTIFIED
    assert c.blocker == "SETTLEMENT_NOT_ESTIMATED"
    assert c.ev_usd is None
    # NOT ZERO. This is the whole point.
    assert "NOT a value of zero" in c.uncertainty


def test_a_bounded_hold_reports_an_interval_and_refuses_a_midpoint():
    mk = MV.Market(payout_low=0.2, payout_high=0.8)
    c = MV.value_hold(pos(100.0, 45.0), mk)
    assert c.ev_usd is None, "a bound must not become a point estimate"
    assert "[-25.0000, 35.0000]" in c.why
    assert "invent a distribution" in c.uncertainty


# ── exits are priced, and the depth limit is honoured ────────────────

def test_exit_prices_against_the_bid_net_of_fees():
    mk = MV.Market(bid=0.50, bid_size=1000)
    c = MV.value_exit(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert c.status == MV.IDENTIFIED
    # 0.50*100 = 50.00 gross, fee 0.01*100*0.50 = 0.50, so 49.50 net,
    # releasing the whole 45.00 basis.
    assert abs(c.cash_now_usd - 49.50) < 1e-9
    assert abs(c.ev_usd - 4.50) < 1e-9
    assert c.requires_our_fill is False, (
        "selling into a displayed bid is a TAKER action and must not "
        "claim to need a fill probability")


def test_a_short_bid_releases_basis_ONLY_on_what_it_could_sell():
    """THE OVERSTATEMENT THIS PREVENTS. Releasing basis on the size we
    WANTED rather than the size the bid could take would credit the exit
    with capital still tied up in contracts we still hold."""
    mk = MV.Market(bid=0.50, bid_size=25)        # wanted 100, got 25
    c = MV.value_exit(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert c.status == MV.IDENTIFIED
    # sold 25 at 0.50 = 12.50, fee 0.125, net 12.375; basis released is
    # 45 * 25/100 = 11.25, NOT 45.
    assert abs(c.cash_now_usd - 12.375) < 1e-9
    assert abs(c.ev_usd - 1.125) < 1e-9
    assert "DEPTH-LIMITED" in c.why


def test_a_missing_fee_schedule_makes_the_exit_unpriced_not_free():
    c = MV.value_exit(pos(), MV.Market(bid=0.5, bid_size=999), fee_fn=None)
    assert c.status == MV.NOT_IDENTIFIED
    assert c.blocker == "FEE_SCHEDULE_NOT_ESTABLISHED"


def test_a_bid_without_depth_is_not_an_exit():
    c = MV.value_exit(pos(), MV.Market(bid=0.5, bid_size=0),
                      fee_fn=flat_fee())
    assert c.blocker == "NO_EXECUTABLE_DEPTH"


def test_reduce_is_the_same_cash_flow_at_a_smaller_size():
    mk = MV.Market(bid=0.50, bid_size=1000)
    half = MV.value_exit(pos(100.0, 45.0), mk, fee_fn=flat_fee(),
                         fraction=0.5)
    assert half.action == MV.REDUCE
    assert abs(half.ev_usd - 2.25) < 1e-9       # exactly half the exit


# ── completion: the forecast-free action, and its three refusals ──────

def test_completion_pays_par_and_carries_no_settlement_forecast():
    mk = MV.Market(comp_ask=0.48, comp_ask_size=1000, comp_source="OBSERVED")
    c = MV.value_complete_pair(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert c.status == MV.IDENTIFIED
    # cost 0.48*100 = 48.00 + fee 0.01*100*0.48 = 0.48, so 48.48; the
    # 100 pairs pay 100.00; basis released 45.00.
    #   100.00 - 48.48 - 45.00 = 6.52
    assert abs(c.ev_usd - 6.52) < 1e-9
    assert abs(c.cash_now_usd - -48.48) < 1e-9
    assert abs(c.cash_at_settlement_usd - 100.0) < 1e-9
    assert "no settlement forecast enters" in c.uncertainty


def test_a_derived_complement_is_BLOCKED_not_merely_unpriced():
    """It is an identity: own + derived = 1 + spread, always."""
    mk = MV.Market(bid=0.50, comp_ask=0.52, comp_ask_size=999,
                   comp_source="DERIVED")
    c = MV.value_complete_pair(pos(), mk, fee_fn=flat_fee())
    assert c.status == MV.BLOCKED
    assert c.blocker == "COMPLEMENT_IS_DERIVED_NOT_OBSERVED"
    assert "3,732" in c.why


def test_an_absent_complement_names_the_verified_payload_shape():
    c = MV.value_complete_pair(pos(), MV.Market(), fee_fn=flat_fee())
    assert c.status == MV.NOT_IDENTIFIED
    assert c.blocker == "COMPLEMENT_ABSENT"
    # The refusal cites what was actually verified, not a guess.
    assert "marketSlug" in c.why


def test_partial_completion_leaves_the_remainder_unvalued_and_says_so():
    """THE DOUBLE COUNT THIS PREVENTS. Valuing all 100 contracts at par
    when only 30 could be paired would price 70 naked directional
    contracts as if they were risk-free."""
    mk = MV.Market(comp_ask=0.48, comp_ask_size=30, comp_source="OBSERVED")
    c = MV.value_complete_pair(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert abs(c.cash_at_settlement_usd - 30.0) < 1e-9
    assert "remain UNPAIRED and directional" in c.why


# ── the comparison, and how it refuses to act ────────────────────────

def test_an_unpriced_hold_selects_NOTHING_even_with_a_priced_exit():
    """THE FAILURE MODE THIS BLOCKS, and it is subtle. Live, HOLD has no
    value and EXIT has one. Ranking by EV alone would select EXIT every
    time -- not because exiting is good but because it is the only
    action with a number. That is an engine that liquidates everything
    for want of a settlement model."""
    mk = MV.Market(bid=0.60, bid_size=1000)      # no payout at all
    out = MV.compare(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert out["selected"] is None
    assert "no action selected" in out["selection_reason"]
    exits = [c for c in out["candidates"] if c["action"] == MV.EXIT_NOW]
    assert exits[0]["status"] == MV.IDENTIFIED, (
        "the control: EXIT really was priced, so the refusal above is "
        "about the unpriced baseline and not about an empty table")


def test_hold_wins_when_settlement_beats_the_bid():
    mk = MV.Market(bid=0.50, bid_size=1000,
                   payout=1.0, payout_source=MV.SETTLED_FACT)
    out = MV.compare(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert out["selected"] == MV.HOLD
    assert "not beaten" in out["selection_reason"]


def test_completion_is_selected_when_it_beats_holding():
    """CONTROL for the two tests above: the comparison can select
    something other than HOLD, so its preference for HOLD is a
    calculation and not a hard-coded bias."""
    # Our leg settles worthless (payout 0), so holding loses the basis;
    # completing locks par and is better.
    mk = MV.Market(bid=0.10, bid_size=1000,
                   comp_ask=0.48, comp_ask_size=1000,
                   comp_source="OBSERVED",
                   payout=0.0, payout_source=MV.SETTLED_FACT)
    out = MV.compare(pos(100.0, 45.0), mk, fee_fn=flat_fee())
    assert out["selected"] == MV.COMPLETE_PAIR, out["selection_reason"]


def test_wait_and_reprice_appear_in_the_table_and_are_refused():
    """An action MISSING from the table reads as one nobody considered.
    An action present and refused reads as one we cannot value."""
    out = MV.compare(pos(), MV.Market(payout=1.0,
                                      payout_source=MV.SETTLED_FACT),
                     fee_fn=flat_fee())
    acts = {c["action"]: c for c in out["candidates"]}
    assert set(acts) >= {MV.HOLD, MV.EXIT_NOW, MV.REDUCE,
                         MV.COMPLETE_PAIR, MV.WAIT, MV.REPRICE}
    assert acts[MV.WAIT]["status"] == MV.NOT_IDENTIFIED
    assert "NOT zero" in acts[MV.WAIT]["uncertainty"]
    assert acts[MV.REPRICE]["blocker"] == "P_FILL_NOT_IDENTIFIED"
    assert acts[MV.REPRICE]["requires_our_fill"] is True


def test_the_comparison_never_claims_a_fill_probability():
    out = MV.compare(pos(), MV.Market(bid=0.5, bid_size=99,
                                      payout=1.0,
                                      payout_source=MV.SETTLED_FACT),
                     fee_fn=flat_fee())
    assert out["p_fill"] == MV.NOT_IDENTIFIED
    # And no SELECTED action may depend on one.
    sel = out["selected"]
    if sel is not None:
        c = next(c for c in out["candidates"] if c["action"] == sel)
        assert c["requires_our_fill"] is False
