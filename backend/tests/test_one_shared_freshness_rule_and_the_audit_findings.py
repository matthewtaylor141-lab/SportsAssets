"""ONE ADMISSION RULE FOR THREE LANES, AND THE TWO AUDIT FINDINGS.

§1  the shared freshness contract: entry, funded activation and funded exits
    consume `bettor_venue_currency` and no lane has its own idea of fresh.
§2  measured observations are separated from policy allowances.
§3  the fee rounding mode is UNRECONCILED, with counterexamples that decide it.
§4  the cumulative taker-fill adjustment is UNRECONCILED and marks multi-fill
    expectations PROVISIONAL.
§5  activation's required-limit map matches enforcement's, and
    `daily_loss_stop_usd` is labelled for what it actually governs.
"""

from __future__ import annotations

import email.utils
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_venue_currency as vc
from sportsassets.workers import ext_pinnacle_loop as loop

_MD_OK = {"marketSlug": "aec-shared-rule",
          "bids": [{"price": "0.40", "qty": "300"}],
          "offers": [{"price": "0.45", "qty": "300"}],
          "state": "OPEN"}


def _md(stamp_epoch):
    md = dict(_MD_OK)
    md["transactTime"] = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(stamp_epoch))
        + ".000000000Z")
    return md


# ── §1 · ONE RULE, THREE LANES ──────────────────────────────────────

def test_the_three_lanes_share_one_bound_and_one_rule():
    """No lane may admit a book the others would refuse."""
    assert loop.MAX_VENUE_QUOTE_AGE_S == FA.MAX_VENUE_BOOK_AGE_S
    assert FA.MAX_VENUE_BOOK_AGE_S == vc.MAX_BOOK_STATE_AGE_S == 30.0
    # AND THE FUNDED PATH SAYS WHOSE RULE IT IS, on every answer.
    got = FA.venue_book_age(_md(time.time() - 3.0), decision_at=time.time(),
                            received_at=time.time() - 0.5,
                            requested_at=time.time() - 0.8)
    assert got["shared_admission_rule"] == "bettor_venue_currency.evaluate"
    assert set(got["policy"]["shared_with"]) == {
        "workers.ext_pinnacle_loop (entry)",
        "bettor_funded_management (exits)",
        "bettor_funded_activation (this)"}


def test_the_funded_path_no_longer_admits_on_a_recent_stamp_alone():
    """THE DEFECT THE SHARED RULE CLOSES ON THE FUNDED SIDE.

    A `transactTime` three seconds old used to return ok=True. What that field
    denotes is UNRESOLVED, so a recent value is not an upstream-freshness
    certificate. With no mechanism supplied the result is the NAMED UNRESOLVED
    one -- and it is explicitly not the claim that the book is stale.
    """
    now = time.time()
    got = FA.venue_book_age(_md(now - 3.0), decision_at=now,
                            received_at=now - 0.5, requested_at=now - 0.9)
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_CURRENCY_NOT_ESTABLISHED
    assert got["unmeasured"] is True
    assert "missing evidence" in got["this_is_not_a_stale_book"]
    assert got["refusal"] != FA.R_BOOK_STALE, (
        "an absence of evidence must not be reported as an observed stale book")
    # The stamp's age is still measured and reported.
    assert got["measured"]["venue_stamp_age_at_decision_s"] == pytest.approx(
        3.0, abs=1.0)


def test_the_funded_path_refuses_a_fast_cached_response_as_contradicted():
    """THE FAST-BUT-OLD CASE, ON THE FUNDED LANE TOO.

    A recent stamp, an instant round trip, a decision half a second after
    receipt -- and a response the origin generated 400 s ago and a cache served.
    Refused, and refused on EVIDENCE rather than on absence.
    """
    now = time.time()
    got = FA.venue_book_age(
        _md(now - 2.0), decision_at=now, received_at=now - 0.5,
        requested_at=now - 0.55,
        observation={"headers": {
            "date": email.utils.formatdate(now - 1.0, usegmt=True),
            "age": "400", "x-cache": "HIT"}})
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_CURRENCY_CONTRADICTED
    assert got["book_currency"]["verdict"] == vc.CONTRADICTED
    assert got["measured"]["acquisition_s"] == pytest.approx(0.05, abs=0.02)


def test_the_funded_path_admits_when_a_mechanism_establishes_currency():
    now = time.time()
    got = FA.venue_book_age(
        _md(now - 100.0),          # an OLD stamp, which must not refuse
        decision_at=now, received_at=now - 0.5, requested_at=now - 0.8,
        subscription={"alive_at": now - 1.0, "last_update_at": now - 4.0})
    assert got["ok"] is True, got["why"]
    assert got["book_currency"]["mechanism"] == vc.M1_LIVE_SUBSCRIPTION
    assert got["measured"]["established_book_state_age_s"] == pytest.approx(
        4.0, abs=0.5)
    assert "reported, not relied on" in got["why"]


def test_the_funded_path_keeps_its_own_extra_checks():
    """THE SHARED RULE ADDS REQUIREMENTS AND REMOVES NONE.

    A stamp far in the future is still an inconsistent clock, and that refusal
    fires BEFORE the currency question -- an hour in the future is not an hour
    fresh whatever mechanism is offered.
    """
    now = time.time()
    got = FA.venue_book_age(
        _md(now + 600.0), decision_at=now, received_at=now - 0.5,
        requested_at=now - 0.8,
        subscription={"alive_at": now - 1.0, "last_update_at": now - 2.0})
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_CLOCK_INCONSISTENT


def test_a_malformed_stamp_is_unmeasured_not_stale():
    now = time.time()
    md = dict(_MD_OK)
    md["transactTime"] = "not-a-timestamp"
    got = FA.venue_book_age(md, decision_at=now, received_at=now - 0.5)
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_AGE_UNMEASURED
    assert got["basis"] == "VENUE_CLOCK_UNPARSEABLE"


def test_an_absent_stamp_is_unmeasured_not_stale():
    now = time.time()
    got = FA.venue_book_age(dict(_MD_OK), decision_at=now,
                            received_at=now - 0.5)
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_AGE_UNMEASURED
    assert got["basis"] == "VENUE_CLOCK_NOT_PROVIDED"


def test_acquisition_delay_counts_against_the_bound(monkeypatch):
    """A SLOW ACQUISITION DOES NOT VANISH. The age is taken at the decision
    instant, so a book 20 s old on arrival is 45 s old at a decision 25 s
    later, and the bound does not move to accommodate it."""
    now = time.time()
    got = FA.venue_book_age(
        _md(now - 45.0), decision_at=now, received_at=now - 25.0,
        requested_at=now - 26.0,
        subscription={"alive_at": now - 1.0, "last_update_at": now - 2.0})
    # The SUBSCRIPTION established the state 2 s ago, so the book's currency is
    # fine -- and the 25 s we then sat on it is recorded, under our own name,
    # for the entry lane's processing-delay gate to act on.
    assert got["ok"] is True, got["why"]
    assert got["measured"]["decision_lag_after_receipt_s"] == pytest.approx(
        25.0, abs=1.0)
    assert got["measured"]["acquisition_s"] == pytest.approx(1.0, abs=0.2)
    # AND THE STAMP BEING PAST THE BOUND IS RECORDED AND DOES NOT REFUSE.
    assert got["stamp_beyond_the_bound"] is True
    assert "unresolved" in got["stamp_age_does_not_decide"]


def test_an_established_state_past_the_bound_is_stale_and_says_so():
    """R_BOOK_STALE, EARNED. A subscription reports this market's last update
    was 90 s ago: that IS an observation that the book is old, and it keeps the
    word `stale` because a mechanism measured it."""
    now = time.time()
    got = FA.venue_book_age(
        _md(now - 2.0), decision_at=now, received_at=now - 0.5,
        subscription={"alive_at": now - 1.0, "last_update_at": now - 90.0})
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_STALE
    assert "past the 30 s bound" in got["why"]


# ── §2 · OBSERVATIONS AND ALLOWANCES, SEPARATED ─────────────────────

def test_measured_observations_are_separated_from_policy_allowances():
    now = time.time()
    got = FA.venue_book_age(
        _md(now - 5.0), decision_at=now, received_at=now - 1.0,
        requested_at=now - 1.4,
        subscription={"alive_at": now - 0.5, "last_update_at": now - 2.0})
    m, p = got["measured"], got["policy"]
    # MEASURED: subtractions between recorded instants.
    assert m["acquisition_s"] == pytest.approx(0.4, abs=0.1)
    assert m["decision_lag_after_receipt_s"] == pytest.approx(1.0, abs=0.1)
    assert m["established_book_state_age_s"] == pytest.approx(2.0, abs=0.5)
    assert "unresolved" in m["what_none_of_these_establish"]
    # POLICY: numbers we chose, labelled as chosen.
    assert p["bound_s"] == 30.0
    assert p["skew_allowance_s"] == FA.MAX_VENUE_CLOCK_SKEW_AHEAD_S
    assert p["these_are_allowances_we_chose"] is True
    # AND NEITHER BLOCK CONTAINS THE OTHER'S FIELDS.
    assert "bound_s" not in m
    assert "acquisition_s" not in p


# ── §3 · THE FEE ROUNDING MODE IS UNRECONCILED ──────────────────────

def test_the_rounding_discriminators_really_differ_by_a_cent():
    """COMPUTED INDEPENDENTLY OF THE IMPLEMENTATION.

    Each vector is re-derived here from the documented formula with both
    rounding modes, so this test cannot pass merely because the module is
    self-consistent -- which is exactly how the `min(p, 1-p)` defect survived
    its first review.
    """
    from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

    from sportsassets import calibration_fees as CF

    cent = Decimal("0.01")
    for q, price, raw, hu, he in CF.ROUNDING_DISCRIMINATORS:
        got = CF.TAKER * Decimal(q) * Decimal(price) * (1 - Decimal(price))
        assert got == Decimal(raw), (q, price, str(got))
        assert got.quantize(cent, rounding=ROUND_HALF_UP) == Decimal(hu)
        assert got.quantize(cent, rounding=ROUND_HALF_EVEN) == Decimal(he)
        assert abs(Decimal(hu) - Decimal(he)) == cent


def test_the_rounding_question_is_now_settled_against_the_published_page():
    """WHAT THIS TEST USED TO ASSERT, AND WHY IT CHANGED.

    It asserted that every expected fee carried `roundingVerified: False` and
    that no report might call the arithmetic exact -- correct while the page
    could not be read from this container. The page WAS read, on the GitHub
    runner, on 2026-09-27: "All fees and rebates are rounded to the nearest $0.01
    using banker's rounding (round half to even)."

    So the audit was right, this module was wrong, and the mode is half-even. The
    exactness claim is now made, and bounded to what was verified -- the detailed
    checks live in `test_the_fee_policy_matches_the_published_page.py`.
    """
    from sportsassets import calibration_fees as CF

    got = CF.expected_fee("0.39", 10)
    assert got["FEE"] is not None
    assert got["rounding"] == "ROUND_HALF_EVEN"
    u = got["roundingUnreconciled"]
    assert u["verified"] is True
    assert "round half to even" in u["the_published_sentence"]
    # BOTH ANSWERS STILL TRAVEL WITH THE ANSWER, so a reconciliation against an
    # older collected fee can see which mode produced it.
    assert "FEE_IF_HALF_EVEN" in got and "FEE_IF_HALF_UP" in got
    assert got["roundingModesAgree"] is True        # 0.39 is not a tie
    # EXACTNESS IS CLAIMED, AND BOUNDED.
    assert CF.FEE_ARITHMETIC_IS_EXACT is True
    assert "combo" in " ".join(CF.FEE_ARITHMETIC_EXACTNESS_EXCLUDES)


def test_a_tie_is_flagged_as_material_on_the_answer():
    from decimal import Decimal

    from sportsassets import calibration_fees as CF

    got = CF.expected_fee("0.50", 120)
    # THE PUBLISHED MODE'S ANSWER IS NOW THE ANSWER. It was 2.09 under half-up,
    # which the retrieved page shows was wrong.
    assert got["FEE"] == Decimal("2.08")
    assert got["FEE_IF_HALF_UP"] == Decimal("2.09")
    assert got["FEE_IF_HALF_EVEN"] == Decimal("2.08")
    assert got["roundingModesAgree"] is False
    assert "exact half-cent tie" in got["roundingIsMaterialHere"]


def test_the_published_vectors_are_unaffected_by_the_rounding_question():
    """THE TWO SOURCE-SUPPLIED VECTORS STILL HOLD. They are not ties, so the
    unreconciled mode changes neither of them -- which is what bounds how much
    of the schedule is actually in question."""
    from decimal import Decimal

    from sportsassets import calibration_fees as CF

    a = CF.expected_fee("0.39", 10)
    b = CF.expected_fee("0.50", 100)
    assert a["FEE"] == Decimal("0.17") and a["roundingModesAgree"] is True
    assert b["FEE"] == Decimal("1.74") and b["roundingModesAgree"] is True


# ── §4 · THE CUMULATIVE TAKER-FILL ADJUSTMENT ───────────────────────

def test_a_multi_fill_expectation_is_marked_provisional():
    from sportsassets import calibration_fees as CF

    one = CF.expected_fee("0.45", 50, fills_in_order=1)
    assert one.get("PROVISIONAL") is None, (
        "a single-fill order is unaffected by a cumulative adjustment")

    part = CF.expected_fee("0.45", 50, fill_index=2, fills_in_order=3)
    assert part["PROVISIONAL"] is True
    why = part["provisionalBecause"]
    assert why["verified"] is False
    assert "cumulative" in why["reported_by_the_audit"]
    assert "one cent per fill" in why["consequence_if_the_audit_is_right"]
    assert part["fillsInOrder"] == 3 and part["fillIndex"] == 2
    assert "collected_fee, which is read back" in why["unaffected"]


# ── §5 · THE LIMIT MAP AND THE MISLABELLED WINDOW ───────────────────

def test_activations_required_limits_match_what_enforcement_applies():
    """THE AUDIT FINDING. Four names here against five in enforcement meant an
    owner approving a small pilot could not tighten MAX_EVENT_EXPOSURE at all,
    and it stayed frozen at $1,000 beside a $25 per-order cap."""
    assert sorted(FA.LIMIT_TO_RAIL) == sorted(EX.APPROVED_LIMIT_TO_RAIL)
    assert FA.LIMIT_TO_RAIL == EX.APPROVED_LIMIT_TO_RAIL
    assert "event_exposure_usd" in FA.REQUIRED_LIMITS
    assert len(FA.REQUIRED_LIMITS) == 5


def test_tightening_the_event_rail_is_not_the_same_as_enforcing_it():
    lab = FA.LIMIT_LABELS["event_exposure_usd"]
    assert "cannot see other positions on the same event" in \
        lab["tightening_is_not_enforcing"]


def test_the_daily_loss_stop_is_labelled_for_what_it_actually_governs():
    """IT IS NOT DAILY. The rail's arithmetic applies no date filter and the value never
    resets, so a $20 approval is a cumulative worst-case loss ceiling on the
    open book -- not a per-day stop that lifts tomorrow."""
    lab = FA.LIMIT_LABELS["daily_loss_stop_usd"]
    assert lab["rail"] == "MAX_DRAWDOWN"
    assert "CUMULATIVE" in lab["what_it_actually_is"]
    assert "No daily window exists" in lab["what_it_actually_is"]
    assert "never resets" in lab["what_it_actually_is"]
    assert lab["accurate_synonym_accepted_on_input"] == \
        "cumulative_loss_stop_usd"
    assert "timezone" in lab["a_real_daily_stop_would_need"]


def test_the_claim_is_checked_against_the_computation_not_just_asserted():
    """AND THE LABEL IS TRUE OF THE CODE. `exposure_from_rows`'s drawdown
    references no date, no calendar boundary and no reset -- which is what makes
    "daily" the wrong word."""
    import inspect
    import re

    src = inspect.getsource(EX.exposure_from_rows)
    dd = src[src.index("drawdown"):]
    for word in ("date_trunc", "current_date", "interval '1 day'", "today"):
        assert word not in dd.lower(), (
            "a date bound appeared in the drawdown arithmetic; if a real daily "
            "window was added, LIMIT_LABELS must be corrected with it")
    assert re.search(r"drawdown \+= c", src), (
        "the unmarked-position total-loss term is what makes this a worst-case "
        "ceiling rather than a realised-loss tally")


def test_the_accurate_synonym_is_accepted_without_breaking_the_old_key():
    # The accurate name maps onto the recorded one.
    assert FA.normalise_limit_keys(
        {"cumulative_loss_stop_usd": 20})["daily_loss_stop_usd"] == 20
    # And a value already recorded under the old name is never overwritten.
    both = FA.normalise_limit_keys({"daily_loss_stop_usd": 20,
                                    "cumulative_loss_stop_usd": 999})
    assert both["daily_loss_stop_usd"] == 20


# ── §6 · THE SEAM, AND ITS PRODUCTION DEFAULT ───────────────────────

def test_the_scheduler_supplies_no_mechanism_today_and_says_so():
    """THE HONEST DEFAULT, PINNED.

    Both lanes reach a freshness mechanism through one function. It returns none,
    with the reason and the work required attached -- so the refusal every
    candidate meets is a NAMED blocker with an owner, not a mystery. If this ever
    starts returning a mechanism that is not a real subscription or a real
    revalidation, this test is what fails.
    """
    got = loop.book_currency_evidence("aec-anything")
    assert got["subscription"] is None
    assert got["revalidation"] is None
    # THE REASON IS NOW THE VERIFIED ONE: M1 was checked against the shipped
    # client and the feed cannot supply two of its four preconditions.
    assert "cannot establish currency on this feed" in got["why_none"]
    assert "no sequence number" in got["why_none"]
    assert got["m1"]["status"] == "M1_NOT_AVAILABLE_ON_THIS_FEED"
    # WHAT WOULD CHANGE IT, and it is no longer "wire M1": M1 was verified and
    # the feed cannot supply it. The route now named is the venue publishing a
    # sequence, or the book path emitting a validator (M2).
    assert "sequence" in got["what_would_change_it"]
    assert "ETag" in got["what_would_change_it"]
    assert "not a stale book" in got["consequence_today"]


def test_both_lanes_read_the_mechanism_from_that_one_seam():
    """ONE SEAM, NOT TWO. A second source of freshness evidence is how the two
    lanes come to disagree about what fresh means."""
    import inspect

    src = inspect.getsource(loop)
    # The entry lane's own read, and the funded servicing pass.
    assert src.count("book_currency_evidence(") >= 3
    cyc = inspect.getsource(loop.cycle)
    assert "book_currency_evidence(" in cyc
    svc = inspect.getsource(loop._funded_service)
    assert "book_currency_evidence()" in svc
