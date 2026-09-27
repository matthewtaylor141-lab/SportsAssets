"""DO THE CORRECTED FEES REACH THE PATHS THAT SPEND MONEY?

I reported the fee finding closed on the strength of `calibration_fees` matching
the published page. The module did match. The SYSTEM did not: `order_fees` --
the function carrying the published cumulative cap -- had no production caller,
and the entry planner was not even using `calibration_fees`.

So this file does not test the schedule. `test_the_fee_policy_matches_the_
published_page.py` already does that against the venue's own table. This file
tests the WIRING, because a corrected module reached by nothing is a corrected
module and not corrected fees.

FOUR THINGS IT PINS.

  1  THE REGISTER MATCHES THE SOURCE. Every consumer in `fee_consumers` names a
     call site, and the call site is read out of the file. A path cannot be
     silently rewired without this failing.
  2  THE DEPTH WALK IS PRICED AS AN ORDER, and a maker walk is NOT.
  3  A FUNDED ORDER'S FILLS ARE PRICED AS ONE ORDER, across a restart and
     across a duplicate delivery.
  4  WHAT IS STILL OPEN IS STILL RECORDED AS OPEN. A2 is IMPLEMENTED, not
     VERIFIED, and the two schedules' disagreements are still listed.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_book as FB
from sportsassets import calibration_fees as CF
from sportsassets import fee_consumers as FC

SRC = pathlib.Path(FB.__file__).parent


def taker_fn(qty, price, maker=False):
    """The published taker curve, in the shape `estimate` passes around."""
    return float(CF.expected_fee(price, qty, CF.ROLE_TAKER)["FEE"])


def maker_fn(qty, price, maker=True):
    return float(CF.expected_fee(price, qty, CF.ROLE_MAKER)["FEE"])


# ── 1 · the register is not prose ────────────────────────────────────

def test_every_registered_consumer_still_exists_where_it_says():
    """A REGISTER THAT IS NOT READ OFF THE CODE IS A COMMENT.

    Each row names a component; the component's file must exist and must
    mention the module the row says it uses. This is deliberately weak on
    exactly one axis -- it proves the wiring is named correctly, not that the
    named wiring is correct -- and the tests below cover the behaviour.
    """
    for c in FC.CONSUMERS:
        comp = c["component"].split()[0].split("/")[-1].split(".")[0]
        hits = list(SRC.rglob("%s.py" % comp))
        assert hits, "%s names a component with no file: %s" % (
            c["path"], c["component"])


def test_the_cumulative_function_now_has_the_callers_the_register_claims():
    assert FC.CUMULATIVE_PRODUCTION_CALLERS_WERE == 0, (
        "the finding was that order_fees had no production caller; that fact "
        "stays on the record")
    assert FC.CUMULATIVE_PRODUCTION_CALLERS == 2
    # AND THE CALLERS ARE REAL, read out of the source rather than asserted.
    entry = (SRC / "bettor_entry_execution.py").read_text()
    book = (SRC / "bettor_funded_book.py").read_text()
    assert "CF.order_fees(" in entry, "walk_fee must call order_fees"
    assert "order_expected_fees" in book and "prior_legs" in book


def test_the_two_schedule_modules_disagreements_are_still_recorded():
    """NOT REPAIRED, AND THEREFORE STILL WRITTEN DOWN.

    `bettor_fee_schedule` is a second, older fee implementation that the entry
    planner and every shadow loop still resolve theta through. It agrees on the
    coefficient, which is why no test caught it, and disagrees on the effective
    date and on per-sport theta, which is where it would cost money.
    """
    fields = {d["field"] for d in FC.SCHEDULE_DISAGREEMENTS}
    assert "effective_from" in fields
    assert "theta_taker_is_per_sport" in fields
    assert "multi_fill_taker_algorithm" in fields
    for d in FC.SCHEDULE_DISAGREEMENTS:
        assert d["authority"] == "calibration_fees"
        assert d["published"], "a disagreement needs the published wording"
    assert "ENTRY_PLANNER" in FC.paths_the_correction_misses()
    assert "SHADOW_LOOP" in FC.paths_the_correction_misses()


# ── 2 · the depth walk ───────────────────────────────────────────────

def test_a_three_level_walk_is_priced_as_a_three_fill_ORDER():
    """THE DEFECT, AND THE NUMBER IT WAS WRONG BY.

    20 contracts at each of 0.35, 0.50 and 0.55. Independently rounded the
    three levels sum to $1.01; under the published running cap the order pays
    $1.00. The old expression produced the first number and called it the fee
    the walk actually incurred.
    """
    levels = [{"qty": 20, "price": 0.35},
              {"qty": 20, "price": 0.50},
              {"qty": 20, "price": 0.55}]
    got = EX.walk_fee(levels, 60, taker_fn)
    assert got["schedule_reaches"] is True
    assert got["basis"] == EX.FEE_VIA_PUBLISHED_ORDER_SCHEDULE
    assert got["total"] == pytest.approx(1.00)
    assert got["cap_applied"] is True
    # THE OLD NUMBER IS STILL REPORTED, so the difference is in the record.
    assert got["independent"] == pytest.approx(1.01 / 60, abs=1e-7)
    assert [p["collected"] for p in got["per_fill"]] == [0.32, 0.34, 0.34]
    assert got["per_contract"] < got["independent"]
    # AND THE PER-LEG SEQUENCE IS VISIBLE, which is what a reconciliation
    # compares against -- a total alone cannot be matched fill by fill.
    assert [p["adjusted"] for p in got["per_fill"]] == [False, True, False]


def test_a_maker_walk_is_NOT_given_the_taker_cap():
    """THE CAP IS A TAKER RULE AND THE SAME PAGE SAYS SO.

    "Maker rebates are computed per fill, independently." One caller passes a
    maker-side fee function, so applying the cumulative cap to whatever arrives
    would be a new defect of exactly the kind being removed. The caller's
    function is checked against the published taker curve rather than trusted
    by name.
    """
    levels = [{"qty": 20, "price": 0.35}, {"qty": 20, "price": 0.50}]
    got = EX.walk_fee(levels, 40, maker_fn)
    assert got["schedule_reaches"] is False
    assert got["basis"] == EX.FEE_VIA_CALLER_PER_LEVEL_SUM
    assert "pricing something else" in got["why_not_the_schedule"]
    assert got["cap_applied"] is None
    # THE CALLER'S OWN ANSWER STILL COMES BACK. Refusing to impose the cap is
    # not refusing to price.
    assert got["per_contract"] is not None


def test_a_flat_per_contract_fee_fn_is_also_refused_the_cap():
    """The hand-typed 0.02 flat constant this repository used to run on. It is
    not the published curve at any price, so it must not be capped as if it
    were."""
    levels = [{"qty": 20, "price": 0.50}, {"qty": 20, "price": 0.50}]
    got = EX.walk_fee(levels, 40, lambda qty, price, maker=False: 0.02 * qty)
    assert got["schedule_reaches"] is False
    assert "published TAKER curve" in got["why_not_the_schedule"]


def test_a_fee_fn_that_raises_cannot_abort_the_sizing_decision():
    """A fee question is not allowed to take down an execution estimate."""
    def boom(**_):
        raise RuntimeError("no")
    got = EX.walk_fee([{"qty": 10, "price": 0.5}], 10, boom)
    assert got["schedule_reaches"] is False
    assert got["total"] is None and got["per_contract"] is None


def test_a_single_level_walk_is_the_ordinary_case_and_the_cap_never_binds():
    got = EX.walk_fee([{"qty": 120, "price": 0.50}], 120, taker_fn)
    assert got["schedule_reaches"] is True
    assert got["cap_applied"] is False
    assert got["total"] == pytest.approx(
        float(CF.expected_fee("0.50", 120, CF.ROLE_TAKER)["FEE"]))


def test_estimate_reports_which_arithmetic_answered():
    """The record has to say whose fee it is. A field called
    `fee_per_contract_realised` that might be the published algorithm or might
    be the caller's sum is the ambiguity this replaces."""
    src = (SRC / "bettor_entry_execution.py").read_text()
    assert '"fee_schedule_reaches_this_path"' in src
    assert '"fee_if_levels_were_priced_independently"' in src
    assert "SUM_OF_PER_LEVEL_FEES_DIVIDED_BY_FILLED_QTY" not in src, (
        "the old basis string described the old arithmetic; leaving it in "
        "place would report a capped number under the uncapped name")


# ── 3 · the funded order's fill sequence ─────────────────────────────

def test_a_funded_orders_legs_are_priced_as_one_order():
    """Three 40-lots at 0.50, which is the published worked example. Priced as
    three independent fills the order pays $2.10; as one order it pays
    $2.08."""
    legs = [(40, 0.50), (40, 0.50), (40, 0.50)]
    got = FB.order_expected_fees(legs)
    assert got["BLOCKER"] is None
    assert got["per_fill"] == [0.70, 0.69, 0.69]
    assert got["TOTAL"] == 0.70 + 0.69 + 0.69
    assert sum(got["per_fill"]) < 2.10


def test_each_leg_is_expected_the_increment_attributed_to_IT():
    """THE ARRIVAL ORDER OF THE SEQUENCE IS WHAT `reconcile_fee` NEEDS.

    Walking the order one fill at a time, exactly as `_ingest_locked` does,
    must reproduce the same per-leg amounts as pricing the whole sequence at
    once. If it did not, the expectation would depend on when the worker
    happened to look.
    """
    seq = [(40, 0.50), (40, 0.50), (40, 0.50)]
    whole = FB.order_expected_fees(seq)["per_fill"]
    incremental = []
    for i in range(len(seq)):
        prior = [(q, p, "f%d" % j) for j, (q, p) in enumerate(seq[:i])]
        r = FB.reconcile_fee(seq[i][0], seq[i][1], None, at="2026-09-27",
                             prior_legs=prior)
        incremental.append(r["expected_fee_usd"])
        assert r["cumulative"]["this_leg_index"] == i
        assert r["cumulative"]["order_legs"] == i + 1
    assert incremental == whole


def test_a_restart_reprices_the_fourth_fill_as_the_fourth_fill():
    """RESTART SAFETY, AND WHY IT COMES FROM THE TABLE.

    The sequence is derived from persisted rows, not from a batch variable, so
    a worker that dies after three fills and comes back sees three priors. The
    proof is that supplying those three priors gives the same answer as having
    processed them in one pass.
    """
    seq = [(30, 0.45)] * 4
    in_one_pass = FB.order_expected_fees(seq)["per_fill"]
    priors = [(q, p, "f%d" % i) for i, (q, p) in enumerate(seq[:3])]
    after_restart = FB.reconcile_fee(30, 0.45, None, at="2026-09-27",
                                    prior_legs=priors)
    assert after_restart["expected_fee_usd"] == in_one_pass[3]


def test_a_duplicate_delivery_does_not_promote_a_fill_to_a_later_leg():
    """IDEMPOTENCE OF THE EXPECTATION.

    `_ingest_locked` excludes the fill being ingested from its own priors by
    id. Without that, redelivering fill 2 would find fill 2 among the priors
    and price it as leg 3 -- so a replay would change the money.

    Checked here on the exclusion itself, because that is the whole mechanism:
    the same fill, priced with and without itself in the prior list, must not
    agree -- which is exactly why the exclusion has to exist.
    """
    # 30 @ 0.45 collects [0.52, 0.51, 0.52, 0.51] leg by leg, so leg 2 and
    # leg 3 are DIFFERENT amounts. Three 40-lots at 0.50 would not do: its
    # legs 2 and 3 both collect $0.69, and a test written on those would pass
    # whether the exclusion existed or not.
    seq = [(30, 0.45)] * 4
    priors_correct = [(30, 0.45, "f0")]
    priors_with_self = [(30, 0.45, "f0"), (30, 0.45, "f1")]
    as_leg2 = FB.reconcile_fee(30, 0.45, None, at="2026-09-27",
                               prior_legs=priors_correct)
    as_leg3 = FB.reconcile_fee(30, 0.45, None, at="2026-09-27",
                               prior_legs=priors_with_self)
    assert as_leg2["expected_fee_usd"] != as_leg3["expected_fee_usd"], (
        "if these agreed the exclusion would be unnecessary and this test "
        "would be proving nothing")
    assert as_leg2["expected_fee_usd"] == FB.order_expected_fees(
        seq[:2])["per_fill"][-1]
    # AND THE SOURCE REALLY EXCLUDES IT.
    book = (SRC / "bettor_funded_book.py").read_text()
    assert "if t[2] != fid" in book


def test_omitting_prior_legs_keeps_the_old_single_fill_behaviour():
    """A caller that cannot see the order's history must not have a cap
    invented for it. Silence is not an empty sequence."""
    one = FB.reconcile_fee(40, 0.50, None, at="2026-09-27")
    assert one["cumulative"] is None
    assert one["expected_fee_usd"] == float(
        CF.expected_fee("0.50", 40, CF.ROLE_TAKER)["FEE"])


def test_the_observed_charge_stays_authoritative():
    """THE CAP CHANGES THE EXPECTATION, NEVER THE CASH.

    The venue's own commission is what the account paid, so it is what the
    accounting books. A cumulative expectation that started overriding an
    observed charge would be a worse defect than the one being fixed.
    """
    r = FB.reconcile_fee(40, 0.50, 0.66, at="2026-09-27",
                         prior_legs=[(40, 0.50, "f0"), (40, 0.50, "f1")])
    assert r["observed_fee_usd"] == 0.66
    assert r["booked_fee_usd"] == 0.66, "the observed charge is what is booked"
    assert r["expected_fee_usd"] != 0.66
    assert r["fee_state"] in (FB.FEE_RECONCILED, FB.FEE_DISAGREES)


def test_the_basis_names_the_leg_so_a_row_can_be_audited():
    r = FB.reconcile_fee(40, 0.50, None, at="2026-09-27",
                         prior_legs=[(40, 0.50, "f0")])
    assert "order_fees" in r["fee_basis"]
    assert "leg 2 of 2" in r["fee_basis"]


def test_a_sequence_the_schedule_refuses_falls_back_and_says_so():
    """A refusal must not be dressed as a capped figure."""
    r = FB.reconcile_fee(40, 0.50, None, at="2026-09-27",
                         prior_legs=[(40, 0.0, "f0")])
    assert r["cumulative"]["BLOCKER"]
    assert r["cumulative"]["fell_back_to"] == "single-fill expected_fee"
    assert r["expected_fee_usd"] == float(
        CF.expected_fee("0.50", 40, CF.ROLE_TAKER)["FEE"])


# ── 4 · multi-price legs, which is why order_fees was generalised ────

def test_order_fees_takes_per_fill_prices_and_the_cumulative_is_a_running_sum():
    """A WALK FILLS AT SEVERAL PRICES, and the cap is stated on "the
    cumulative exact fee" -- the sum over fills, not theta x total_qty x one
    price factor. Those coincide only when every fill took the same price,
    and writing it the wrong way is how a multi-price walk gets the wrong
    cap."""
    legs = [(20, "0.35"), (20, "0.50"), (20, "0.55")]
    got = CF.order_fees(None, legs)
    assert got["BLOCKER"] is None
    assert got["one_price"] is False
    assert got["prices"] == ["0.35", "0.50", "0.55"]
    exact = sum(Decimal("0.0695") * 20 * CF.price_factor(p) for _, p in legs)
    assert got["cumulative_exact"] == exact
    # THE WRONG FORM, DEMONSTRATED: one price factor on the total quantity.
    wrong = Decimal("0.0695") * 60 * CF.price_factor("0.35")
    assert wrong != exact


def test_the_single_price_form_is_unchanged():
    """The published worked example still holds, so generalising the signature
    did not move the number the venue's own table pins."""
    got = CF.order_fees("0.50", [40, 40, 40])
    assert got["TOTAL"] == Decimal("2.08")
    assert got["one_price"] is True
    assert [f["collected"] for f in got["per_fill"]] == [
        Decimal("0.70"), Decimal("0.69"), Decimal("0.69")]


def test_a_zero_or_negative_leg_is_refused_not_priced_as_free():
    for legs in ([(0, "0.5")], [(10, "0")], [(-5, "0.5")], []):
        assert CF.order_fees(None, legs)["BLOCKER"] is not None, legs


# ── 5 · the claim about all this stays honest ────────────────────────

def test_A2_is_IMPLEMENTED_and_not_VERIFIED():
    assert FC.A2_STATUS == "IMPLEMENTED"
    assert FC.A2_STATUS_IS_NOT == "VERIFIED"
    why = FC.A2_WHY_NOT_VERIFIED
    assert "DEPLOYED" in why
    assert "observed charge" in why
    assert "never had a real fill" in why


def test_what_is_still_open_is_still_listed():
    open_text = " ".join(FC.STILL_OPEN)
    assert "LATEST" in open_text, (
        "the entry planner still resolves theta through LATEST rather than "
        "the fill's own date")
    assert "per-sport theta is inert" in open_text
    assert "EXIT planner" in open_text
    assert "VERIFIED_APPLIED" in open_text


def test_the_test_venue_executor_reconciles_against_itself():
    """WHY NONE OF THE LIFECYCLE PROOFS SURFACED THE MISSING CAP.

    The substituted transport computes `expected_fees` as a per-fill sum and
    then asserts `fees_reconcile` against its own sum. Both sides come from the
    same arithmetic, so the check passes however wrong that arithmetic is. It
    is recorded because a proof that cannot fail is the reason a defect
    survives, not a detail.
    """
    row = FC.by_path("TEST_VENUE_EXECUTOR")
    assert row is not None
    assert "RECONCILES AGAINST ITSELF" in " ".join(row["defects"])
    assert row["reaches"] == FC.NOT_REACHED
