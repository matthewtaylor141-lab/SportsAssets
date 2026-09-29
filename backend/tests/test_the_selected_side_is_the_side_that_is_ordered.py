"""THE SELECTED SHORT CANDIDATE CANNOT BECOME A LONG ORDER.

── THE DEFECT THIS EXISTS TO END ─────────────────────────────────────

`pass_once` dispatched the acquisition with

    us_market_slug=facts["hedge_us_market_slug"]
    quantity=facts["hedge_quantity"]
    limit_price=facts["hedge_limit_price"]

-- a SEPARATE record from the candidate the ranking selected. The owner's words:
"Bind acquisition to its selected candidate's complete plan as rigorously as
exits. A correctly ranked side is not sufficient if dispatch takes its address or
quantity from a separate record."

AND THERE WAS NO SIDE IN IT AT ALL. One venue slug carries two outcome tokens
with opposite payouts. An order naming only the slug does not say which one it is
buying, so a ranking that correctly picks the SHORT side could produce a LONG
fill and nothing in the record would disagree.

IT WAS ALSO UNREACHABLE, which is why no test caught the missing binding: the
production supplier hardcodes `hedge_us_market_slug`, `hedge_quantity`,
`hedge_limit_price`, `hedge_collateral_usd` and `hedge_decision_record` to None,
so the acquisition branch could not dispatch. `test_the_production_supplier_...`
below pins that as the reason rather than leaving it as folklore.

── WHAT REPLACES IT ──────────────────────────────────────────────────

`AcquisitionPlan` carries the whole order -- account, venue, group,
`candidate_id`, the `venue_slug` and `side` DERIVED from it, action, quantity,
limit, collateral and evidence expiry -- is immutable once built, and hashes all
of that into a `digest`. `acquire_second_leg` refuses a plan whose digest or
candidate does not match what was ranked.

The side is derived, not supplied, so a plan cannot name one candidate and
address another. That is the invariant the first section asserts.
"""

import pytest

from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_settlement_clauses as SC
from sportsassets.workers import ext_pinnacle_loop as LOOP

HELD_SLUG = "aec-mlb-bos-nyy-2026-10-05"
HEDGE_SLUG = "asc-mlb-bos-nyy-2026-10-05-neg-1pt5"
SHORT_ID = "%s#%s" % (HEDGE_SLUG, HS.SIDE_SHORT)
LONG_ID = "%s#%s" % (HEDGE_SLUG, HS.SIDE_LONG)

PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50. If the game is cancelled all stakes are "
         "refunded.")
RULES = SC.interpret(PROSE)["rules"]


def leg(cid, backs, cost_cents, qty=10, kind=IS.KIND_SPREAD, line=None):
    from fractions import Fraction
    return IS.Leg(condition_id=cid, fixture_id="mlb-bos-nyy-2026-10-05",
                  kind=kind, period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED,
                  backs=backs, line=line or Fraction(-3, 2), quantity=qty,
                  cost_cents_per_unit=cost_cents,
                  settlement_text_captured=True, settlement_rules=RULES)


def winner(cid, *, cost_cents=30, units=10):
    return {"condition_id": cid, "taxonomy": "MIDDLE", "units": units,
            "leg": leg(cid, "B" if cid == SHORT_ID else "A", cost_cents,
                       qty=units),
            "structure": {"taxonomy": "MIDDLE", "units": units,
                          "min_payout_cents": 100, "max_payout_cents": 200,
                          "cost_cents": 85, "table": []}}


def row(cid, *, price=0.30, covered=6.0):
    return {"condition_id": cid, "price": price, "covered_qty": covered,
            "uncovered_qty": 4.0}


def plan(cid=SHORT_ID, **kw):
    kw.setdefault("account_id", "acct")
    kw.setdefault("venue", "PMUS")
    kw.setdefault("group_id", "grp-1")
    return PC.acquisition_plan_for(
        winner=winner(cid), ranked_row=row(cid),
        held_position={"us_market_slug": HELD_SLUG, "intent_id": "i-1"},
        inputs_expire_at=1_000_000.0, **kw)


# ═════════════════════════════════════════════════════════════════════
# 1 · A SHORT CANDIDATE CANNOT BECOME A LONG ORDER
# ═════════════════════════════════════════════════════════════════════

def test_the_plan_derives_its_side_from_the_candidate_it_executes():
    p = plan(SHORT_ID)
    assert p.candidate_id == SHORT_ID
    assert p.side == HS.SIDE_SHORT
    assert p.venue_slug == HEDGE_SLUG
    # The venue is addressed by the SLUG; the side is carried beside it.
    assert "#" not in p.venue_slug


def test_the_two_sides_of_one_instrument_produce_two_different_orders():
    short, long_ = plan(SHORT_ID), plan(LONG_ID)
    assert short.side != long_.side
    assert short.venue_slug == long_.venue_slug
    # AND TWO DIFFERENT DIGESTS. Keyed by slug alone they were one order.
    assert short.digest != long_.digest


def test_a_plan_cannot_name_one_candidate_and_address_another():
    """The side is DERIVED, so there is no field to disagree with."""
    p = plan(SHORT_ID)
    assert (p.venue_slug, p.side) == HS.split_identity(p.candidate_id)
    with pytest.raises(AttributeError):
        p.side = HS.SIDE_LONG
    with pytest.raises(AttributeError):
        p.venue_slug = "some-other-slug"
    with pytest.raises(AttributeError):
        p.quantity = 999


def test_a_candidate_with_no_side_cannot_be_turned_into_an_order():
    with pytest.raises(PC.PlanRefused) as exc:
        plan(HEDGE_SLUG)                     # a bare slug, no side
    assert exc.value.refusal == PC.R_ACQ_PLAN_SIDE_MISSING
    assert "does not say what it is buying" in exc.value.why


def test_the_scored_row_and_the_admitted_entry_must_be_the_same_candidate():
    with pytest.raises(PC.PlanRefused) as exc:
        PC.acquisition_plan_for(
            winner=winner(SHORT_ID), ranked_row=row(LONG_ID),
            account_id="acct", venue="PMUS", group_id="grp-1",
            held_position={"us_market_slug": HELD_SLUG},
            inputs_expire_at=1_000_000.0)
    assert exc.value.refusal == PC.R_ACQ_PLAN_NOT_THE_SELECTED_CANDIDATE


@pytest.mark.parametrize("side", list(HS.SIDES))
def test_a_plan_addressing_the_held_instrument_is_refused_as_netting(side):
    """TWO SIDES OF ONE INSTRUMENT REMAIN NETTING, NOT AN INDIRECT PAIR.

    Buying either side of what is already held nets the position. The identity
    is side-aware and this check deliberately is not: it compares venue slugs,
    so the opposing side is caught too.
    """
    cid = "%s#%s" % (HELD_SLUG, side)
    with pytest.raises(PC.PlanRefused) as exc:
        PC.acquisition_plan_for(
            winner=winner(cid), ranked_row=row(cid),
            account_id="acct", venue="PMUS", group_id="grp-1",
            held_position={"us_market_slug": HELD_SLUG},
            inputs_expire_at=1_000_000.0)
    assert exc.value.refusal == PC.R_ACQ_PLAN_NETS_THE_HELD_INSTRUMENT
    assert "nets the position" in exc.value.why


def test_the_discovery_filter_agrees_with_the_plan_filter():
    """The same rule enforced at both ends, on the same legs.

    `discover` rejects the opposing side as netting, and the plan refuses it
    too. Either alone would leave the other as the only guard.
    """
    held = leg("%s#%s" % (HELD_SLUG, HS.SIDE_LONG), "A", 55,
               kind=IS.KIND_MONEYLINE, line=None)
    other_side = leg("%s#%s" % (HELD_SLUG, HS.SIDE_SHORT), "B", 45,
                     kind=IS.KIND_MONEYLINE, line=None)
    got = PC.discover(held_leg=held, candidate_legs=[other_side],
                      sport_permits_tie=False)
    assert got["admitted"] == []
    assert got["rejected"][0]["refusal"] == PC.R_NOT_DISTINCT
    assert got["rejected"][0]["is_the_same_side"] is False


# ═════════════════════════════════════════════════════════════════════
# 2 · THE DIGEST BINDS THE ORDER TO THE RANKING
# ═════════════════════════════════════════════════════════════════════

def test_the_digest_covers_every_field_that_makes_it_this_order():
    assert PC.ACQ_PLAN_REQUIRED == (
        "account_id", "venue", "group_id", "candidate_id", "venue_slug",
        "side", "action", "quantity", "limit_price", "collateral_usd",
        "inputs_expire_at")
    base = plan(SHORT_ID)
    # Changing ANY of them changes the digest, so a substituted plan is caught.
    for field, value in (("account_id", "other-acct"), ("venue", "OTHER"),
                         ("group_id", "grp-2")):
        other = plan(SHORT_ID, **{field: value})
        assert other.digest != base.digest, field


def test_a_different_quantity_or_price_is_a_different_order():
    base = plan(SHORT_ID)
    fewer = PC.acquisition_plan_for(
        winner=winner(SHORT_ID), ranked_row=row(SHORT_ID, covered=3.0),
        account_id="acct", venue="PMUS", group_id="grp-1",
        held_position={"us_market_slug": HELD_SLUG},
        inputs_expire_at=1_000_000.0)
    dearer = PC.acquisition_plan_for(
        winner=winner(SHORT_ID), ranked_row=row(SHORT_ID, price=0.44),
        account_id="acct", venue="PMUS", group_id="grp-1",
        held_position={"us_market_slug": HELD_SLUG},
        inputs_expire_at=1_000_000.0)
    assert fewer.digest != base.digest
    assert dearer.digest != base.digest
    assert fewer.quantity == 3.0 and base.quantity == 6.0
    assert dearer.limit_price == 0.56


def test_the_quantity_is_the_proposed_one_not_the_requested_one():
    """`covered_qty` is what the displayed depth supports. Asking for the
    requested quantity would order contracts the book has not shown."""
    p = plan(SHORT_ID)
    assert p.quantity == 6.0
    assert "depth the book supports" in p.quantity_from


def test_the_price_comes_from_the_candidate_and_says_which_part_of_it():
    quoted = plan(SHORT_ID)
    assert quoted.limit_price == 0.70
    assert "own quoted price" in quoted.price_from
    # With no per-candidate quote -- a supplier that read a shared depth for the
    # position -- the price is the winning LEG's own basis, still the candidate's.
    unquoted = PC.acquisition_plan_for(
        winner=winner(SHORT_ID, cost_cents=41),
        ranked_row={"condition_id": SHORT_ID, "covered_qty": 10.0},
        account_id="acct", venue="PMUS", group_id="grp-1",
        held_position={"us_market_slug": HELD_SLUG},
        inputs_expire_at=1_000_000.0)
    assert unquoted.limit_price == 0.59
    assert "winning leg's own cost_cents_per_unit" in unquoted.price_from


def test_the_collateral_is_computed_from_this_plans_own_price_and_quantity():
    p = plan(SHORT_ID)
    assert p.collateral_usd == pytest.approx(
        FX.collateral_for(0.30, 6.0, FX.LONG))


async def test_a_substituted_plan_is_refused_at_dispatch():
    """The digest check, exercised through the dispatcher."""
    ranked, substituted = plan(SHORT_ID), plan(LONG_ID)
    got = await PC.acquire_second_leg(
        None, operation_id="op-1", group_id="grp-1",
        decision_record={}, account_id="acct", venue="PMUS",
        plan=substituted, expect_digest=ranked.digest, now=0.0)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_ACQ_PLAN_DIGEST
    assert got["submitted"] is False
    assert got["nothing_was_sent"] is True
    assert "a different order" in got["why"]


async def test_a_plan_for_another_candidate_is_refused_at_dispatch():
    got = await PC.acquire_second_leg(
        None, operation_id="op-1", group_id="grp-1",
        decision_record={}, account_id="acct", venue="PMUS",
        plan=plan(LONG_ID), expect_candidate_id=SHORT_ID, now=0.0)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_ACQ_PLAN_NOT_THE_SELECTED_CANDIDATE
    assert got["submitted"] is False
    assert "A correctly ranked side is not enough" in got["why"]


async def test_the_side_reaches_the_order_record():
    """`order_intent` is what the venue is told. Without it the order names a
    market and not an outcome token."""
    p = plan(SHORT_ID)
    seen = {}

    async def _hold(conn, **kw):
        seen.update(kw)
        return {"ok": False, "refusal": "STOPPED_HERE_ON_PURPOSE"}

    import sportsassets.bettor_funded_reservations as RSV
    real = RSV.hold
    RSV.hold = _hold
    try:
        got = await PC.acquire_second_leg(
            None, operation_id="op-1", group_id="grp-1",
            decision_record={}, account_id="acct", venue="PMUS", plan=p,
            expect_digest=p.digest, now=0.0)
    finally:
        RSV.hold = real
    # The reservation is taken for the SLUG -- that is the venue's instrument --
    # and the binding records the side beside it.
    assert seen["us_market_slug"] == HEDGE_SLUG
    assert seen["quantity"] == 6.0
    assert seen["limit_price"] == 0.70
    assert got["order_binding"]["bound"] is True
    assert got["order_binding"]["side"] == HS.SIDE_SHORT
    assert got["order_binding"]["candidate_id"] == SHORT_ID
    assert got["order_binding"]["digest"] == p.digest


async def test_an_unbound_dispatch_is_recorded_as_unbound():
    """A caller that supplies no plan gets its order sent from its own fields --
    and the record says the order was not bound to a ranked candidate. That
    shape is the defect, so it is labelled rather than silently accepted."""
    async def _hold(conn, **kw):
        return {"ok": False, "refusal": "STOPPED_HERE_ON_PURPOSE"}

    import sportsassets.bettor_funded_reservations as RSV
    real = RSV.hold
    RSV.hold = _hold
    try:
        got = await PC.acquire_second_leg(
            None, operation_id="op-1", group_id="grp-1",
            decision_record={}, account_id="acct", venue="PMUS",
            us_market_slug=HEDGE_SLUG, quantity=6.0, limit_price=0.30,
            collateral_usd=1.8, now=0.0)
    finally:
        RSV.hold = real
    assert got["order_binding"]["bound"] is False
    assert "rather than from the candidate that was ranked" in \
        got["order_binding"]["why"]


async def test_an_incomplete_order_sends_nothing():
    got = await PC.acquire_second_leg(
        None, operation_id="op-1", group_id="grp-1",
        decision_record={}, account_id="acct", venue="PMUS", now=0.0)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_PLAN_INCOMPLETE
    assert got["submitted"] is False
    assert got["nothing_was_sent"] is True


# ═════════════════════════════════════════════════════════════════════
# 3 · WHY NOTHING CAUGHT THIS BEFORE
# ═════════════════════════════════════════════════════════════════════

def test_the_production_supplier_still_hardcodes_the_old_hedge_plan_fields():
    """THE REASON THE UNBOUND DISPATCH WAS NEVER EXERCISED.

    `funded_pair_inputs` sets all five old hedge plan fields to None, so the
    acquisition branch could not dispatch at all. That is recorded here because
    "unreachable" is the honest status of the old path, and because the new path
    must not depend on those fields: `pass_once` now builds the plan from the
    ranking winner and never reads them.
    """
    import inspect

    src = inspect.getsource(LOOP.funded_pair_inputs)
    assert "hedge_us_market_slug=None" in src
    assert "hedge_quantity=None" in src
    assert "hedge_limit_price=None" in src

    pass_src = inspect.getsource(PC.pass_once)
    idx = pass_src.index("acquire_second_leg(")
    call = pass_src[idx:idx + 900]
    # THE CALL NO LONGER READS THEM.
    assert 'facts["hedge_us_market_slug"]' not in call, call[:400]
    assert 'facts["hedge_quantity"]' not in call
    assert 'facts["hedge_limit_price"]' not in call
    # IT READS THE PLAN AND CHECKS IT AGAINST THE RANKING.
    assert "plan=acq_plan" in call
    assert "expect_candidate_id=cid" in call
    assert 'expect_digest=selected["plan_digest"]' in call
