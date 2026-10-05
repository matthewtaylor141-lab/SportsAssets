"""THE COMPLETE SCHEDULED PAIR LIFECYCLE, ON SUBSTITUTED TRANSPORT.

╔════════════════════════════════════════════════════════════════════╗
║  ENGINEERING PROOF ON SUBSTITUTED TRANSPORT.                       ║
║  NO VENUE IS CONTACTED. NO FUNDED ORDER IS SENT.                   ║
║  REAL-VENUE VERIFICATION IS A SEPARATE MILESTONE requiring the     ║
║  authorized account and its limits.                                ║
╚════════════════════════════════════════════════════════════════════╝

Owner directive, item 5: "Make the next milestone a complete scheduled
lifecycle … demonstrate: discovery of two distinct settlement-compatible
contracts on one fixture; comparison against real HOLD/EXIT/REDUCE
alternatives; capital reservation and second-leg acquisition; partial fill,
lost acknowledgement and restart without duplicate submission; management of
remaining unpaired inventory; settlement or exit of both legs; reconciled
group economics and correctly attributed learning records; the actual
decision and outstanding risks visible in Command Centre. Use substituted
transport for the engineering proof and label it clearly."

────────────────────────────────────────────────────────────────────
WHAT IS DEPLOYED CODE HERE AND WHAT IS A CHOSEN INPUT.

DEPLOYED, and entered from the scheduled lane's own module:

    bettor_funded_pair_cycle.recover_reservations   restart recovery
    bettor_funded_pair_cycle.discover               settlement compatibility
      -> bettor_indirect_structures.classify        the payoff regions
    bettor_funded_pair_cycle.decide_and_record      one comparison
      -> bettor_funded_decision.decide              EV ranking, limits first
      -> bettor_funded_learning.record_decision     the prospective row
    bettor_funded_pair_cycle.acquire_second_leg     reserve, then send
      -> bettor_funded_reservations.hold            the leg claim
      -> bettor_funded_execution.submit_for_decision  rails, gate, switch,
                                                      and the four atomic
                                                      boundaries
    bettor_funded_pair_cycle.reconcile_and_learn    group economics once
    bettor_funded_pair_cycle.operator_view          the command-centre read
    bettor_funded_book.command_center               the operator surface

CHOSEN, and every one of them is an INPUT this file supplies rather than a
fact anything observed:

  * the two contracts and their prices,
  * the probability over the fixture's outcome regions,
  * the bid ladder, its depth and the fee,
  * the venue's answers to every request -- including the partial fill, the
    lost acknowledgement and the order id recovery later finds.

SO WHAT THIS ESTABLISHES, EXACTLY: that the shipped software carries a funded
pair from discovery through a reserved, atomically-bound acquisition, a
partial fill, a lost acknowledgement, a restart that does NOT resend, the
management of the unpaired remainder, settlement of both legs, one reconciled
group result and a correctly attributed learning record -- and that an
operator can read the decision and every outstanding risk.

IT ESTABLISHES NOTHING ABOUT OPPORTUNITY OR PROFITABILITY. Not that this
middle existed on any venue, not that it was priced this way, not that either
leg would have filled. The dollar figures are arithmetic over prices chosen
here.

    "PRODUCTION CODE EXERCISED UNDER CONTROLLED INPUTS" IS NOT
    "PRODUCTION FUNDED BEHAVIOR VERIFIED".

────────────────────────────────────────────────────────────────────
HOW THE EXCLUSION AND THE SWITCHES ARE HANDLED.

The account id carries `DEMONSTRATION`, so `bettor_funded_book` classifies
every book it returns as out of strategy performance AT THE READER, not in a
footnote. `FUNDED_SUBMISSION_ENABLED` stays False in the shipped code and is
monkeypatched True inside these tests -- the established pattern -- and a test
below asserts the shipped constant is still False.
"""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_reservations as RSV
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_indirect_structures as IS

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

LABEL = ("ENGINEERING PROOF ON SUBSTITUTED TRANSPORT -- "
         "NO VENUE CONTACTED, NO FUNDED ORDER SENT")

#: THE MARK THAT KEEPS THIS OUT OF PERFORMANCE.
ACCT = "acct-funded-DEMONSTRATION-pairlife"
VENUE = "PMUS"

#: ── THE FIXTURE AND ITS TWO CONTRACTS ───────────────────────────────
#:
#: The two legs are `bettor_indirect_structures`' OWN fixture contracts --
#: the Bears moneyline and the Panthers +4.5 -- so the structure under test is
#: the one that module established rather than a pair hand-rolled here. An
#: earlier attempt at this suite built its own spreads and got GAP where it
#: expected MIDDLE; using the module's fixtures removes that class of mistake.
FIXTURE = IS.BEARS_PANTHERS_FIXTURE
EVENT = "ev-chi-car-2026-09-13"
SLUG_PRIMARY = "aec-nfl-chi-car-2026-09-13-chi-ml"
SLUG_HEDGE = "aec-nfl-chi-car-2026-09-13-car-plus-4h"
PAYS_ON = "CHICAGO_BEARS"

PRIMARY_QTY = 10
PRIMARY_PX = 0.62
HEDGE_QTY = 10
HEDGE_PX = 0.41
HEDGE_FEE = 0.30

LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}
EMPTY_VENUE = {"held_usd": 0.0, "working_usd": 0.0, "unresolved_usd": 0.0}

#: THE GROUP ID `record_intent` DERIVES for a primary leg that names none:
#: "grp:" + the intent id. Read off the writer's own rule rather than passed in,
#: because passing a group id that does not exist is refused outright --
#: `THE_PORTFOLIO_GROUP_DOES_NOT_EXIST`, which is the 131 trigger working.
PRIMARY_INTENT = "fpi-pairlife-primary"
GROUP = "grp:" + PRIMARY_INTENT
OP_HEDGE = "op:pairlife-hedge"
OP_LOST = "op:pairlife-lost"
DECISION = "dec:pairlife-1"


# ════════════════════════════════════════════════════════════════════
# THE SUBSTITUTED TRANSPORT
# ════════════════════════════════════════════════════════════════════

class _Orders:
    """THE VENUE'S ORDER SURFACE, SUBSTITUTED. Every answer is chosen here.

    `create` counts its calls, and that counter is the instrument for the
    no-duplicate-submission claim: recovery must not increment it.
    """

    def __init__(self, sent, *, executions=None, order_id="venue-ord",
                 raise_on_create=None):
        self.sent = sent
        self._exec = executions
        self._oid = order_id
        self._raise = raise_on_create
        self.creates = 0

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
        px = float((req.get("price") or {}).get("value") or 0)
        qty = int(req.get("quantity") or 0)
        return {"order": {"price": req.get("price"),
                          "quantity": req.get("quantity"),
                          "cashOrderQty": {"value": "%.4f" % (px * qty),
                                           "currency": "USD"}}}

    def create(self, params):
        self.sent.append(("create", dict(params)))
        self.creates += 1
        if self._raise is not None:
            raise self._raise
        return {"id": "%s-%d" % (self._oid, self.creates),
                "executions": list(self._exec or ())}

    def list(self, params=None):
        self.sent.append(("list", dict(params or {})))
        return {"orders": []}

    def retrieve(self, order_id):
        self.sent.append(("retrieve", order_id))
        return None

    def cancel(self, order_id, body=None):
        self.sent.append(("cancel", order_id))
        return {}


class _Client:
    def __init__(self, sent, **kw):
        self.orders = _Orders(sent, **kw)


def _transport(monkeypatch, **kw):
    """SUBSTITUTED AT `pmus._get_client`, so every layer above it is the
    deployed one -- including the adapter's own gate, which is also
    substituted because it would otherwise refuse before any transport."""
    from sportsassets import pmus
    sent: list = []
    client = _Client(sent, **kw)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    # ── THREE SWITCHES, EACH ONE A SEPARATE BOUNDARY ────────────────
    #
    # `REAL_ORDER_SUBMISSION_ENABLED` is the global execution gate's; without it
    # the gate refuses with THE_EXECUTION_GATE_DID_NOT_AFFIRMATIVELY_ALLOW_IT
    # before any intent is written -- which is the correct shipped behaviour and
    # is asserted by its own tests. `FUNDED_SUBMISSION_ENABLED` is this
    # connector's. `pmus._gate.authorize` is the adapter's own venue-boundary
    # gate. All three are off in the shipped code; a test below asserts that.
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
    # R30A: the canonical-origination boundary, stated as satisfied
    from tests.admission_fixture import assume_canonical_funded_origination
    assume_canonical_funded_origination(monkeypatch)
    return pmus, sent, client


def _fill(qty, px, *, vid="vf-1", state="ORDER_STATE_PARTIALLY_FILLED"):
    return {"id": vid, "type": "EXECUTION_TYPE_FILL",
            "lastPx": {"value": "%.2f" % px, "currency": "USD"},
            "lastShares": qty, "order": {"state": state}}


# ════════════════════════════════════════════════════════════════════
# SEEDING
# ════════════════════════════════════════════════════════════════════

async def _seed(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-pairlife','ACTIVE',FALSE,'RECONCILED',0,'pair lifecycle')",
        ACCT)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": dict(LIMITS), "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(dict(LIMITS))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "pairlife",
                    "at": now, "expires_at": now + 3600.0, "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))
    return eff


async def _clean(conn):
    for op in (OP_HEDGE, OP_LOST):
        await conn.execute(
            "DELETE FROM bettor_funded_operation_evidence WHERE operation_id=$1",
            op)
    await conn.execute(
        "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN "
        "(SELECT decision_id FROM bettor_funded_decisions WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_decisions WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_group_results WHERE group_id IN "
        "(SELECT group_id FROM bettor_funded_portfolio_groups "
        "  WHERE account_id=$1)", ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_leg_reservations WHERE group_id IN "
        "(SELECT group_id FROM bettor_funded_portfolio_groups "
        "  WHERE account_id=$1)", ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_economics WHERE intent_id IN "
        "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_discrepancies WHERE intent_id IN "
        "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN "
        "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1", ACCT)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


async def _primary(conn, *, intent_id=PRIMARY_INTENT):
    """THE HELD FIRST LEG, through the book's own writer, in a real group."""
    coll = FX.collateral_for(PRIMARY_PX, PRIMARY_QTY, FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG_PRIMARY,
        event_key=EVENT, order_intent=FX.LONG, limit_price=PRIMARY_PX,
        quantity=PRIMARY_QTY, collateral_usd=coll, effective_digest="d",
        payout_event=PAYS_ON, held_is_long=True,
        # NO GROUP ID: the writer creates the group and this leg TOGETHER, in
        # one transaction, and names it after the intent. An INDIRECT_MIDDLE
        # structure so the group is one a second leg may join -- the triggers
        # then require the account, venue and fixture to match.
        portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    assert got.get("portfolio_group_id") == GROUP, got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="venue-primary-1",
                                    status="open")
    await FB.ingest_fills(conn, intent_id,
                          [{"qty": float(PRIMARY_QTY), "price": PRIMARY_PX,
                            "venue_fill_id": "vf-primary-1"}])
    return intent_id


# ════════════════════════════════════════════════════════════════════
# THE INPUTS, BUILT FROM THE DEPLOYED MODULES' OWN SHAPES
# ════════════════════════════════════════════════════════════════════

#: SIDE-AWARE IDENTITIES, because a real candidate has one and an order needs
#: it. `condition_id` is `slug#SIDE`: the slug is what the venue is addressed
#: with and the side is which of its two outcome tokens is being bought. Without
#: the side, `acquisition_plan_for` refuses -- correctly -- because an order
#: naming only the slug does not say what it would buy, and a ranking that picks
#: the SHORT side could produce a LONG fill.
HELD_ID = "%s#%s" % (SLUG_PRIMARY, HS.SIDE_LONG)
HEDGE_ID = "%s#%s" % (SLUG_HEDGE, HS.SIDE_SHORT)


def _held_leg():
    return dataclasses.replace(IS.BEARS_MONEYLINE, condition_id=HELD_ID,
                               quantity=PRIMARY_QTY,
                               cost_cents_per_unit=int(PRIMARY_PX * 100))


def _hedge_leg():
    return dataclasses.replace(IS.PANTHERS_PLUS_4_5, condition_id=HEDGE_ID,
                               quantity=HEDGE_QTY,
                               cost_cents_per_unit=int(HEDGE_PX * 100))


def _decoys():
    """THREE CANDIDATES THAT MUST BE REJECTED, EACH FOR A DIFFERENT REASON.

    Without these, "two distinct settlement-compatible contracts" would be a
    claim about a list of length one rather than about a filter.
    """
    same = _held_leg()                              # not distinct
    other_fixture = dataclasses.replace(_hedge_leg(),
                                        condition_id="0xother-fixture",
                                        fixture_id="nfl-somewhere-else")
    other_period = dataclasses.replace(_hedge_leg(),
                                       condition_id="0xfirst-half",
                                       period=IS.PERIOD_H1)
    return [same, other_fixture, other_period]


def _region_probabilities(table, *, p_middle=0.50):
    """A PROBABILITY ON EVERY ONE OF THE CLASSIFIER'S OWN REGIONS.

    The module refuses an expectation over a partition with any unpriced cell,
    so this fills every region -- a region left out would be silently treated
    as impossible, and the module's own refusal exists to stop that.
    """
    probs = {r["region"]: 0.0 for r in table}
    probs["margin in (0, 4)"] = p_middle
    probs["margin > 5"] = round(1.0 - p_middle, 9)
    return probs


def _hold_ranking():
    """HOLD, DIRECT_EXIT AND REDUCE, in `rank_with_hold`'s own candidate shape.

    Hand-built, and that is a stated limit of this proof: `rank_with_hold` has
    its own suite and its own inputs (an `ev_hold` record and a venue ladder),
    and reproducing them here would test the selector rather than the
    lifecycle. What matters for item 5 is that the indirect acquisition is
    compared against REAL alternatives carrying their own value, downside,
    incremental capital, capital duration and evidence quality -- not against
    omissions. All three are priced below.
    """
    return {
        "version": "MGMT_SELECT_SHAPE",
        "candidates": [
            # HOLD: 10 contracts at a 0.55 settlement probability on a $6.20
            # basis. Positive, and its worst case is losing the whole stake --
            # the exact position a minimax policy would liquidate.
            {"action": "HOLD", "qty": 10, "value_usd": 1.80,
             "expected_net_usd": 1.80, "downside_usd": -6.20,
             "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
             "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
             "execution_secured": True},
            {"action": "DIRECT_EXIT", "qty": 10, "value_usd": 0.40,
             "expected_net_usd": 0.40, "downside_usd": 0.40,
             "incremental_capital_usd": 0.0, "capital_duration_h": 0.0,
             "evidence_quality": FD.EVIDENCE_VENUE_IMPLIED,
             "execution_secured": False},
            {"action": "REDUCE", "qty": 5, "value_usd": 1.10,
             "expected_net_usd": 1.10, "downside_usd": -3.10,
             "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
             "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
             "execution_secured": False},
        ],
        "not_rankable": [],
    }


def _hedge_decision_record():
    """THE SECOND LEG'S ORDER, as a decision record `plan_from_decision` reads.

    The same object shape the shadow path produces, so the funded path and the
    shadow path cannot disagree about what a decision said.
    """
    return {
        "admissible": True, "refusals": [],
        "us_market_slug": SLUG_HEDGE, "event_key": EVENT,
        "order_intent": FX.LONG, "payout_event": "CAROLINA_PANTHERS_PLUS_4_5",
        "execution_plan": {"execution": {"size": HEDGE_QTY, "vwap": HEDGE_PX,
                                         "limit_price": HEDGE_PX}},
    }


def _structure(*, p_middle=0.50):
    st = IS.classify(_held_leg(), _hedge_leg(), sport_permits_tie=False,
                     fixture_can_postpone=False)
    assert st.taxonomy == IS.MIDDLE, st.why
    return st


def _admitted():
    st = _structure()
    return {"condition_id": _hedge_leg().condition_id,
            "taxonomy": st.taxonomy, "units": st.units,
            "min_payout_cents": st.min_payout_cents,
            "max_payout_cents": st.max_payout_cents,
            "structure": st.to_dict(), "leg": _hedge_leg()}


def _candidate(*, p_middle=0.50, fee=HEDGE_FEE, depth_qty=25):
    st = _structure()
    wc = FIP.net_worst_case(st, fee_usd=fee, fee_basis="CHOSEN_FOR_THIS_PROOF")
    return FD.indirect_candidate(
        structure=st.to_dict(),
        region_probabilities=_region_probabilities(st.table,
                                                   p_middle=p_middle),
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=fee,
        depth=FIP.depth_supports(wanted_qty=HEDGE_QTY,
                                 depth_qty_at_price=depth_qty),
        incremental=FIP.incremental_capital_usd(
            hedge_qty=HEDGE_QTY, hedge_price=HEDGE_PX, hedge_fee_usd=fee),
        capital_duration_h=26.0, worst_case=wc)


# ════════════════════════════════════════════════════════════════════
# 0 · THE LABEL AND THE SWITCHES, ASSERTED BEFORE ANYTHING ELSE
# ════════════════════════════════════════════════════════════════════

def test_the_shipped_submission_switch_is_still_disabled():
    """THE MONKEYPATCH BELOW MUST NOT BE MISTAKEN FOR A RELEASE."""
    import importlib

    mod = importlib.reload(
        importlib.import_module("sportsassets.bettor_funded_execution"))
    assert mod.FUNDED_SUBMISSION_ENABLED is False
    ex = importlib.reload(
        importlib.import_module("sportsassets.bettor_entry_execution"))
    assert ex.REAL_ORDER_SUBMISSION_ENABLED is False


def test_this_demonstration_account_is_classified_out_of_performance():
    """AT THE READER EVERY CONSUMER USES, not in a footnote here."""
    cls = FB.classify_book(ACCT)
    assert cls["counts_toward_strategy_performance"] is False
    assert FB.is_demonstration_account(ACCT) is True


def test_the_pair_cycle_names_its_production_importers():
    d = PC.describe()
    assert "bettor_funded_reservations" in d["imports_in_production"]
    assert "bettor_funded_decision" in d["imports_in_production"]
    assert "bettor_funded_learning" in d["imports_in_production"]
    assert d["submission_switch"].endswith("= False")


def test_the_scheduled_worker_actually_calls_this_module():
    """THE CLAIM THAT MAKES THESE MODULES PRODUCTION CODE, checked against the
    worker's source rather than asserted in prose."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as W

    src = inspect.getsource(W._funded_service)
    assert "bettor_funded_pair_cycle" in src
    assert "pass_once" in src


# ════════════════════════════════════════════════════════════════════
# 1 · DISCOVERY: TWO DISTINCT SETTLEMENT-COMPATIBLE CONTRACTS
# ════════════════════════════════════════════════════════════════════

def test_discovery_admits_one_second_contract_and_rejects_three():
    """TWO CONTRACTS ON ONE FIXTURE, AND THE FILTER IS WHAT IS TESTED.

    The three rejections each name a different reason, so "settlement
    compatible" is a property the code checks rather than a label on a list.
    """
    got = PC.discover(held_leg=_held_leg(),
                      candidate_legs=_decoys() + [_hedge_leg()],
                      sport_permits_tie=False, fixture_can_postpone=False)
    assert got["ok"] is True, got
    assert got["examined"] == 4
    assert got["distinct_settlement_compatible_contracts"] == 2
    assert [a["condition_id"] for a in got["admitted"]] == [
        _hedge_leg().condition_id]
    refusals = {r["refusal"] for r in got["rejected"]}
    assert PC.R_NOT_DISTINCT in refusals, (
        "the contract already held is the SAME holding; on a one-instrument "
        "venue its opposite side is netting, not a second settling position")
    assert PC.R_NOT_SETTLEMENT_COMPATIBLE in refusals
    # AND THE ADMITTED STRUCTURE IS A REAL MIDDLE, from the classifier.
    st = got["admitted"][0]
    assert st["taxonomy"] == IS.MIDDLE
    assert st["units"] == 10
    assert st["min_payout_cents"] == 100 and st["max_payout_cents"] == 200


def test_a_different_period_is_rejected_by_the_grading_key_not_the_title():
    """A FULL-GAME LEG AND A FIRST-HALF LEG ARE GRADED AGAINST DIFFERENT
    VARIABLES however similar their titles look."""
    half = dataclasses.replace(_hedge_leg(), condition_id="0xh1",
                               period=IS.PERIOD_H1)
    got = PC.discover(held_leg=_held_leg(), candidate_legs=[half],
                      sport_permits_tie=False, fixture_can_postpone=False)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_NO_SECOND_CONTRACT
    bad = got["rejected"][0]
    assert bad["refusal"] == PC.R_NOT_SETTLEMENT_COMPATIBLE
    assert bad["held_grading_key"] != bad["candidate_grading_key"]


def test_an_unestablished_structure_is_rejected_with_its_missing_facts():
    """AND IT IS NOT ADMITTED WITH A FLOOR OVER THE REGIONS IT COULD ESTABLISH.
    An uncaptured settlement rule is the case that produced a fabricated hedge
    with a decimal point on it once already."""
    vague = dataclasses.replace(_hedge_leg(), condition_id="0xvague",
                                overtime=IS.OT_UNKNOWN)
    got = PC.discover(held_leg=_held_leg(), candidate_legs=[vague],
                      sport_permits_tie=False, fixture_can_postpone=False)
    assert got["ok"] is False
    bad = got["rejected"][0]
    assert bad["refusal"] in (PC.R_NOT_SETTLEMENT_COMPATIBLE,
                             FD.R_STRUCTURE_IS_UNESTABLISHABLE)


# ════════════════════════════════════════════════════════════════════
# 2 · THE COMPARISON, AGAINST REAL HOLD / EXIT / REDUCE
# ════════════════════════════════════════════════════════════════════

def test_the_indirect_acquisition_is_compared_against_all_three_alternatives():
    """ONE COMPARISON, FOUR ACTIONS, ONE UNIT.

    Every alternative carries its own expected value, downside, incremental
    capital, capital duration and evidence quality, and the five stay separate.
    """
    got = FD.decide(hold_ranking=_hold_ranking(), indirect=_candidate(),
                    capital_duration_h=26.0)
    actions = [c["action"] for c in got["candidates"]]
    assert set(actions) == {"HOLD", "DIRECT_EXIT", "REDUCE",
                            FD.ACTION_ACQUIRE_INDIRECT_HEDGE}, actions
    assert got["not_rankable"] == [], (
        "no alternative is excluded here: an omission would manufacture the "
        "preference this test exists to rule out")
    assert got["policy"] == "EXPECTED_NET_VALUE"
    assert got["worst_case_is"] == "A_CONSTRAINT_NOT_THE_SORT_KEY"
    # THE SELECTION IS ON EXPECTED VALUE: $4.40 against HOLD's $1.80.
    assert got["selected"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE
    five = got["the_five_quantities"]
    assert five["HOLD"]["downside_usd"] == pytest.approx(-6.20)
    assert five[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["downside_usd"] == \
        pytest.approx(-0.60)
    assert five[FD.ACTION_ACQUIRE_INDIRECT_HEDGE][
        "incremental_capital_usd"] == pytest.approx(4.40)


def test_the_hold_wins_when_the_middles_probability_does_not_justify_it():
    """THE SAME COMPARISON THE OTHER WAY, so the preference is a function of
    the inputs and not of the code's shape. At a 10% middle the acquisition's
    expected net falls below HOLD's and HOLD is selected."""
    got = FD.decide(hold_ranking=_hold_ranking(),
                    indirect=_candidate(p_middle=0.10))
    assert got["selected"] == "HOLD"
    picked = {c["action"]: c for c in got["candidates"]}
    assert picked[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["expected_net_usd"] < \
        picked["HOLD"]["expected_net_usd"]


def test_an_unread_depth_makes_the_acquisition_not_rankable_not_zero():
    """A PARTIAL HEDGE IS A DIFFERENT POSITION FROM THE ONE VALUED, and an
    unknown depth is not an unlimited one."""
    got = FD.decide(hold_ranking=_hold_ranking(),
                    indirect=_candidate(depth_qty=4))
    blocked = {b["action"]: b for b in got["not_rankable"]}
    assert blocked[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["blocker"] == \
        FD.R_DEPTH_NOT_ESTABLISHED
    assert blocked[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["value_usd"] is None
    assert got["selected"] == "HOLD"


# ════════════════════════════════════════════════════════════════════
# 3 · THE WHOLE SCHEDULED LIFECYCLE, IN ORDER, AGAINST THE DATABASE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_complete_scheduled_pair_lifecycle(monkeypatch):
    """ONE RUN THROUGH EVERY STAGE ITEM 5 NAMES, in the order a cycle does it.

    Each stage asserts the CONSEQUENCE, not that a function returned. The two
    that matter most are marked in place: exposure counted exactly once across
    every transition, and no duplicate submission across the restart.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    trace: dict = {"label": LABEL, "transport": "SUBSTITUTED",
                   "venue_contacted": False, "stages": []}

    def _stage(name, **facts):
        trace["stages"].append(dict(facts, stage=name))

    try:
        await _clean(conn)
        await _seed(conn)

        # ── STAGE 1 · THE HELD FIRST LEG ────────────────────────────
        primary = await _primary(conn)
        held = await FB.open_entry_positions(conn, account_id=ACCT,
                                            venue=VENUE)
        assert len(held) == 1 and held[0]["intent_id"] == primary
        assert held[0]["portfolio_group_id"] == GROUP
        _stage("held_first_leg", intent_id=primary, group_id=GROUP,
               qty=PRIMARY_QTY, price=PRIMARY_PX)

        # ── STAGE 2 · DISCOVERY ─────────────────────────────────────
        found = PC.discover(held_leg=_held_leg(),
                            candidate_legs=_decoys() + [_hedge_leg()],
                            sport_permits_tie=False,
                            fixture_can_postpone=False)
        assert found["distinct_settlement_compatible_contracts"] == 2
        _stage("discovery", admitted=len(found["admitted"]),
               rejected=[r["refusal"] for r in found["rejected"]],
               taxonomy=found["admitted"][0]["taxonomy"])

        # ── STAGE 3 · THE DECISION, RECORDED BEFORE ITS OUTCOME ─────
        dec = await PC.decide_and_record(
            conn, decision_id=DECISION, account_id=ACCT, venue=VENUE,
            fixture=FIXTURE, group_id=GROUP, hold_ranking=_hold_ranking(),
            admitted=found["admitted"][0],
            region_probabilities=_region_probabilities(
                found["admitted"][0]["structure"]["table"]),
            evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED,
            fee_usd=HEDGE_FEE,
            depth=FIP.depth_supports(wanted_qty=HEDGE_QTY,
                                     depth_qty_at_price=25),
            incremental=FIP.incremental_capital_usd(
                hedge_qty=HEDGE_QTY, hedge_price=HEDGE_PX,
                hedge_fee_usd=HEDGE_FEE),
            capital_duration_h=26.0,
            holding_policy=FL.POLICY_HOLD_TO_SETTLEMENT,
            filled_qty=float(PRIMARY_QTY))
        assert dec["ok"] is True, dec
        assert dec["action"] == PC.ACTION_ACQUIRE
        assert dec["policy"] == "EXPECTED_NET_VALUE"
        row = await conn.fetchrow(
            "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
            DECISION)
        assert row is not None
        assert row["action"] == "ACQUIRE_HEDGE"
        assert row["realised_known"] is False, (
            "the decision is written BEFORE the outcome exists; that is what "
            "makes its claim falsifiable")
        assert row["bound_holding_policy"] == FL.POLICY_HOLD_TO_SETTLEMENT
        assert float(row["bound_filled_qty"]) == pytest.approx(PRIMARY_QTY)
        _stage("decision", action=dec["action"],
               worst_case_usd=dec["selected"].get("downside_usd"),
               expected_net_usd=dec["selected"].get("expected_net_usd"),
               ranked=[c["action"] for c in dec["decision"]["candidates"]])

        # ── STAGE 4 · RESERVATION, THEN A LOST ACKNOWLEDGEMENT ──────
        #
        # THE ACQUISITION IS RESERVED FIRST AND THE SEND'S ANSWER IS LOST. This
        # is the hardest state in the machine: the order MAY be live at the
        # venue, so the exposure stands, the leg stays claimed, and nothing may
        # be resent. `_Orders.creates` counts submissions and every later stage
        # checks it has not moved.
        _, sent, client = _transport(
            monkeypatch, order_id="venue-hedge",
            raise_on_create=TimeoutError("the answer never came back"))
        before = await RSV.reserved_collateral_usd(conn, account_id=ACCT)
        assert before["ok"] is True and before["reserved_usd"] == 0.0

        got = await PC.acquire_second_leg(
            conn, operation_id=OP_HEDGE, group_id=GROUP,
            us_market_slug=SLUG_HEDGE, quantity=HEDGE_QTY,
            limit_price=HEDGE_PX,
            collateral_usd=FX.collateral_for(HEDGE_PX, HEDGE_QTY, FX.LONG),
            decision_record=_hedge_decision_record(), account_id=ACCT,
            venue=VENUE, venue_positions=EMPTY_VENUE)
        assert got["submitted"] is True, got
        assert got["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT, got
        assert got["exposure"] == "PRESERVED"
        hedge_intent = got["intent_id"]
        assert hedge_intent is not None
        assert client.orders.creates == 1
        sub = got["submission"]
        # ── BOUNDARIES 1, 2 AND 3, EACH OBSERVED ────────────────────
        assert sub["reservation_bind"]["ok"] is True
        assert sub["reservation_bind"]["exposure_is_now_counted_on"] == \
            hedge_intent, (
            "the bind must report the STORED intent, which is the field whose "
            "echoing of the argument was the reproduced replay defect")
        assert sub["reservation_send_eligible"]["ok"] is True
        assert sub["reservation_ambiguous"]["ok"] is True, \
            sub["reservation_ambiguous"]
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            hedge_intent) == "UNRESOLVED"
        res = await RSV.get(conn, OP_HEDGE)
        assert res["reservation"]["state"] == RSV.AMBIGUOUS, (
            "the intent and the reservation are the same fact about the same "
            "order, and they must not disagree about whether it may exist")
        # ── EXPOSURE COUNTED EXACTLY ONCE, MID-FLIGHT ───────────────
        #
        # AMBIGUOUS is past HELD, so the reservation stops counting and the
        # intent row carries the exposure. Counting both would double every
        # in-flight acquisition at the moment the lane decides what it can
        # afford -- the direction that spends money it does not have.
        mid = await RSV.reserved_collateral_usd(conn, account_id=ACCT)
        assert mid["reserved_usd"] == 0.0, mid
        assert [c["operation_id"] for c in mid["live_but_not_counted"]] == \
            [OP_HEDGE]
        assert mid["live_but_not_counted"][0][
            "counted_on_the_intent_instead"] == hedge_intent
        _stage("reservation_and_lost_acknowledgement", operation_id=OP_HEDGE,
               intent_state="UNRESOLVED",
               reservation_state=res["reservation"]["state"],
               exposure=got["exposure"],
               submissions_so_far=client.orders.creates,
               reserved_usd_counted_once=mid["reserved_usd"])

        # ── STAGE 5 · THE RESTART, WITHOUT A DUPLICATE SUBMISSION ───
        #
        # A NEW CONNECTION, to make the point that nothing in process memory is
        # carrying this state. Recovery runs first, as the cycle runs it.
        creates_before_restart = client.orders.creates
        conn2 = await asyncpg.connect(DSN)
        try:
            # 5a · WITH NO EVIDENCE, IT REFUSES AND THE LEG STAYS CLAIMED.
            rec1 = await PC.recover_reservations(conn2, account_id=ACCT)
            assert rec1["ok"] is True, rec1
            assert rec1["resubmitted_anything"] is False
            waiting = {w["operation_id"] for w in rec1["awaiting_evidence"]}
            assert OP_HEDGE in waiting, rec1
            still = await RSV.get(conn2, OP_HEDGE)
            assert still["reservation"]["state"] == RSV.AMBIGUOUS
            assert still["reservation"]["collateral_usd"] > 0

            # 5b · AN EMPTY OPEN-ORDERS READ DOES NOT CLEAR IT EITHER.
            #
            # THIS IS THE FAILURE THE EVIDENCE TABLE IS SHAPED AROUND: an order
            # that filled immediately is not OPEN, so a search of the open book
            # was never capable of finding it, and "nothing found" is a
            # conclusion drawn from a query that could not have produced it.
            open_only = await RSV.record_venue_evidence(
                conn2, evidence_id="ev:open-only:%s" % OP_HEDGE,
                operation_id=OP_HEDGE, account_id=ACCT, venue=VENUE,
                us_market_slug=SLUG_HEDGE, intent_id=hedge_intent,
                kind=RSV.EV_NO_SUCH_ORDER, search_endpoint="orders.list",
                search_scope={"state": "OPEN_ONLY"},
                covered_terminal_orders=False, results_returned=0,
                read_at=time.time())
            assert open_only["ok"] is False
            assert open_only["refusal"] == \
                RSV.R_EVIDENCE_SEARCH_COULD_NOT_HAVE_FOUND_IT
            assert (await RSV.get(conn2, OP_HEDGE))[
                "reservation"]["state"] == RSV.AMBIGUOUS

            # 5c · THE VENUE'S OWN ANSWER, RECORDED, RESOLVES IT.
            named = await RSV.record_venue_evidence(
                conn2, evidence_id="ev:named:%s" % OP_HEDGE,
                operation_id=OP_HEDGE, account_id=ACCT, venue=VENUE,
                us_market_slug=SLUG_HEDGE, intent_id=hedge_intent,
                kind=RSV.EV_NAMED, venue_order_id="venue-hedge-recovered",
                search_endpoint="orders.list",
                search_scope={"account_id": ACCT,
                              "us_market_slug": SLUG_HEDGE,
                              "includes": "TERMINAL"},
                covered_terminal_orders=True, results_returned=1,
                read_at=time.time())
            assert named["ok"] is True, named
            rec2 = await PC.recover_reservations(conn2, account_id=ACCT)
            assert rec2["resubmitted_anything"] is False
            resolved = {r["operation_id"] for r in rec2["resolved"]}
            assert OP_HEDGE in resolved, rec2
            final = await RSV.get(conn2, OP_HEDGE)
            assert final["reservation"]["state"] == RSV.CONSUMED
        finally:
            await conn2.close()
        # ── NO DUPLICATE SUBMISSION ─────────────────────────────────
        assert client.orders.creates == creates_before_restart == 1, (
            "recovery resolved an ambiguous acquisition by ASKING. A resend "
            "here is the duplicate order this whole machine exists to stop")
        _stage("restart_recovery", resubmitted_anything=False,
               submissions_total=client.orders.creates,
               refused_without_evidence=True,
               refused_an_open_only_search=True,
               resolved_from=RSV.EV_NAMED,
               reservation_state=RSV.CONSUMED)

        # ── STAGE 6 · THE LATE PARTIAL FILL, AND WHAT STAYS UNPAIRED ─
        #
        # The venue's record says the order filled 6 of the 10 contracts and
        # then terminated. That execution arrives AFTER the restart, so it is
        # ingested now -- and the result is a pair whose second leg covers only
        # six of the ten units the structure was valued on.
        #
        # STATED SCOPE: the CORRELATION step -- proving a venue order is ours
        # before adopting it -- is `bettor_funded_book.recover`'s, and it has
        # its own suite. Ingesting through `record_acknowledgement` and
        # `ingest_fills` here exercises the same two writers `recover` calls
        # without re-testing correlation in a file about the pair lifecycle.
        await FB.record_acknowledgement(
            conn, hedge_intent, venue_order_id="venue-hedge-recovered",
            status="filled")
        ing = await FB.ingest_fills(
            conn, hedge_intent,
            [{"qty": 6.0, "price": HEDGE_PX, "venue_fill_id": "vf-hedge-late"}])
        assert ing["filled_qty_from_the_ledger"] == pytest.approx(6.0)
        filled = await conn.fetchval(
            "SELECT coalesce(sum(qty),0)::float8 FROM bettor_funded_fills "
            " WHERE intent_id=$1", hedge_intent)
        assert filled == pytest.approx(6.0)
        # ── THE UNPAIRED REMAINDER IS A FACT, NOT A ROUNDING ────────
        #
        # Ten primary contracts, six hedged. Four are naked, and the structure's
        # floor was computed for TEN matched units -- so the bound that decision
        # claimed no longer describes this position. `bound_filled_qty` is on the
        # decision row precisely so that divergence reads as INVALIDATED rather
        # than as the settlement model having been wrong.
        unpaired = PRIMARY_QTY - filled
        assert unpaired == pytest.approx(4.0)
        # AND A SECOND HEDGE LEG CANNOT SIMPLY BE SOUGHT: migration 131 permits
        # one OPEN entry per role per group, and this hedge holds inventory. The
        # refusal is the bound working, and naming it is the honest answer to
        # "what happens to the remaining four".
        second = await FB.record_intent(
            conn, intent_id="fpi-pairlife-hedge-2", account_id=ACCT,
            venue=VENUE, venue_class=FA.VENUE_FUNDED,
            us_market_slug=SLUG_HEDGE, event_key=EVENT, order_intent=FX.LONG,
            limit_price=HEDGE_PX, quantity=4, collateral_usd=1.64,
            effective_digest="d", payout_event="CAROLINA_PANTHERS_PLUS_4_5",
            held_is_long=True, portfolio_group_id=GROUP, leg_role="HEDGE")
        assert second["ok"] is False
        assert second["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE, second

        # ── THE ORDER IS STILL WORKING, AND THAT IS A SEPARATE FACT ──
        #
        # A partial fill leaves the order OUTSTANDING: six contracts are held
        # and four are still being worked at the venue. Order terminality and
        # inventory closure are two different questions, and this lane keeps
        # them apart deliberately. The remainder is stopped through the deployed
        # cancel path, `bettor_funded_management.cancel_outstanding`, which
        # refuses to infer a cancellation the venue did not confirm.
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            hedge_intent) == "PARTIALLY_FILLED"
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        cancelled = await FM.cancel_outstanding(conn, intent_id=hedge_intent)
        assert cancelled["ok"] is True, cancelled
        assert cancelled["residual_qty"] == pytest.approx(6.0), (
            "a cancel after a partial fill leaves the FILLED contracts in "
            "exposure; only a zero residual closes the position")
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            hedge_intent) == "CANCELLED"
        _stage("late_fill_and_unpaired_inventory",
               hedge_filled_qty=filled, primary_qty=PRIMARY_QTY,
               unpaired_qty=unpaired,
               units_the_bound_was_claimed_on=10,
               a_second_hedge_leg_is_refused_by=second["refusal"],
               remainder_cancelled=True,
               inventory_still_held_after_the_cancel=cancelled["residual_qty"])

        # ── STAGE 7 · THE OPERATOR SURFACE, MID-LIFECYCLE ───────────
        view = await PC.operator_view(conn, account_id=ACCT)
        assert view["ok"] is True, view
        assert view["last_decision"]["decision_id"] == DECISION
        assert view["last_decision"]["action"] == "ACQUIRE_HEDGE"
        assert view["reserved"]["ok"] is True
        # ── THE UNPAIRED REMAINDER IS ON THE PANEL, BY QUANTITY ─────
        #
        # SIX OF TEN HEDGED, so four contracts are naked -- and this risk used to
        # be keyed on `count(legs) = 1`, which made a group with a partially
        # filled hedge read as PAIRED and produced no risk at all. That is how
        # this was caught: the trace printed `risk_count: 0` here.
        naked = [r for r in view["risks"] if r["risk"] == PC.RISK_UNPAIRED]
        assert naked, view["risks"]
        assert naked[0]["group_id"] == GROUP
        assert naked[0]["primary_residual_qty"] == pytest.approx(10.0)
        assert naked[0]["hedge_residual_qty"] == pytest.approx(6.0)
        assert naked[0]["unpaired_qty"] == pytest.approx(4.0)
        # THE MESSAGE NAMES THE QUANTITY AND THE NAKED LEG. This asserted the
        # phrase "MATCHED units", which the residual repair replaced with a
        # sentence reading remaining inventory rather than historical entry
        # volume. The numbers above are the claim; the text has to agree with
        # them, not reproduce one wording of them.
        meaning = naked[0]["what_it_means"]
        assert "4.0" in meaning and "PRIMARY" in meaning, meaning
        _stage("operator_view", risk_count=view["risk_count"],
               unpaired_qty=naked[0]["unpaired_qty"],
               risks=sorted({r["risk"] for r in view["risks"]}),
               last_decision=view["last_decision"]["action"])

        # ── STAGE 8 · SETTLEMENT OF BOTH LEGS ───────────────────────
        #
        # The Bears won by more than 4.5, so the moneyline pays and the spread
        # does not -- which is what the fixture ACTUALLY resolved to, and it is
        # the region OUTSIDE the middle. A demonstration that paid both legs
        # would be showing the best cell of the table as the result.
        assert IS.BEARS_PANTHERS_ACTUAL["both_win_occurred"] is False
        await FB.record_economic_event(
            conn, intent_id=primary, kind="SETTLEMENT",
            amount_usd=float(PRIMARY_QTY), basis="CHOSEN_FOR_THIS_PROOF")
        await FB.mark_position_closed(conn, primary, "SETTLED_BY_THE_VENUE")
        await FB.record_economic_event(
            conn, intent_id=hedge_intent, kind="SETTLEMENT",
            amount_usd=0.0, basis="CHOSEN_FOR_THIS_PROOF")
        await FB.mark_position_closed(conn, hedge_intent, "SETTLED_BY_THE_VENUE")
        _stage("settlement", legs_settled=2,
               resolution=IS.BEARS_PANTHERS_ACTUAL["resolved"],
               both_win_occurred=False)

        # ── STAGE 9 · ONE RECONCILED GROUP RESULT, AND THE LEARNING ─
        # BOTH LEGS ARE CLOSED BEFORE ANYTHING IS RECONCILED. `join_realised`
        # refuses while a position is still open -- a partial result compared
        # against a whole-structure bound would report a violation that is only
        # incompleteness -- and a partially-filled ORDER stays outstanding until
        # it is cancelled, which is why stage 6 had to cancel the remainder.
        states = [dict(r) for r in await conn.fetch(
            "SELECT intent_id, state, residual_qty::float8 AS residual, "
            "       closed_at IS NOT NULL AS closed, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "                                      closed_at) AS is_open "
            "  FROM bettor_funded_intents WHERE portfolio_group_id=$1", GROUP)]
        assert not [r for r in states if r["is_open"]], states
        rl = await PC.reconcile_and_learn(conn, group_id=GROUP,
                                          decision_ids=(DECISION,))
        assert rl["ok"] is True, rl
        book = rl["group_result"]
        assert book["ok"] is True, book
        # THE GROUP'S RESULT IS RECORDED ONCE, and portfolio P&L sums THAT.
        rows = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_group_results "
            " WHERE group_id=$1", GROUP)
        assert rows == 1
        assert rl["portfolio_pnl_is_summed_from"] == \
            "bettor_funded_group_results"
        # AND THE DECISION CARRIES THE GROUP'S RESULT AS A VERSIONED OUTCOME.
        outcomes = await conn.fetch(
            "SELECT version, realised_net_usd::float8 AS net, is_final "
            "  FROM bettor_funded_decision_outcomes WHERE decision_id=$1 "
            " ORDER BY version", DECISION)
        assert len(outcomes) == 1 and outcomes[0]["version"] == 1
        scored = await FL.score(conn, account_id=ACCT, require_final=False)
        assert scored["ok"] is True, scored
        assert scored["decisions_recorded"] == 1
        # ── THE PORTFOLIO FIGURE IS THE GROUP'S, NOT THE DECISIONS' SUM ──
        pf = scored["portfolio_pnl"]
        assert pf["basis"] == ("ONE ROW PER GROUP, from "
                               "bettor_funded_group_results")
        assert pf["groups_with_a_result"] == 1
        assert [g["group_id"] for g in pf["per_group"]] == [GROUP]
        assert pf["realised_net_total_usd"] == pytest.approx(
            float(book["realised_net_usd"]))
        # ── AND THE BOUND'S SCOPE WAS INVALIDATED, NOT VIOLATED ─────
        #
        # The floor was claimed for TEN matched units held to settlement. Six
        # were acquired. That divergence makes the comparison void -- it is NOT
        # evidence the settlement model was wrong, and filing it as a violation
        # would be filing the wrong finding.
        assert scored["bounds_violated"] == 0, scored["violations"]
        assert scored["bounds_invalidated_by_a_later_divergence"] == 1, scored
        assert "which_action_was_right" in scored["not_reported"]
        _stage("reconciled_economics",
               group_results_rows=rows,
               realised_net_usd=book.get("realised_net_usd"),
               outcome_versions=len(outcomes),
               portfolio_groups=scored["portfolio_pnl"]["groups_with_a_result"],
               bounds_violated=scored["bounds_violated"],
               bounds_invalidated=scored[
                   "bounds_invalidated_by_a_later_divergence"])

        # ── STAGE 10 · THE COMMAND CENTRE ───────────────────────────
        cc = await FB.command_center(conn)
        assert cc["pair_lane"]["ok"] is True, cc["pair_lane"]
        lane = cc["pair_lane"]
        ours = [d for d in lane["recent_decisions"]
                if d["decision_id"] == DECISION]
        assert ours, "the decision this lifecycle took is not on the panel"
        assert ours[0]["action"] == "ACQUIRE_HEDGE"
        assert ours[0]["bound_scope"]["holding_policy"] == \
            FL.POLICY_HOLD_TO_SETTLEMENT
        assert "risks_are_not_summed" in lane
        # ── THE BOUND CHECK ON THE PANEL IS NOT SILENT ──────────────
        #
        # The floor was claimed for ten matched units held to settlement; six
        # were acquired. The panel must say INVALIDATED -- and must not say HELD,
        # which is what it said while nothing supplied the observed scope.
        assert ours[0]["bound_check"] == FL.CHECK_INVALIDATED, ours[0]
        assert ours[0]["observed_scope"]["ok"] is True
        assert ours[0]["observed_scope"]["filled_qty"] == pytest.approx(6.0)
        assert any(r["risk"] == PC.RISK_BOUND_INVALIDATED
                   for r in lane["risks"]), lane["risks"]
        # AND THIS BOOK IS STILL CLASSIFIED OUT OF PERFORMANCE.
        demo = [b for b in cc["demonstration_books"]
                if b["account_id"] == ACCT]
        assert demo and demo[0][
            "counts_toward_strategy_performance"] is False
        _stage("command_centre", pair_lane_ok=True,
               decision_on_the_panel=True,
               bound_check=ours[0]["bound_check"],
               classified_out_of_performance=True)

        # ── THE WHOLE RUN, IN ONE PLACE ─────────────────────────────
        assert [s["stage"] for s in trace["stages"]] == [
            "held_first_leg", "discovery", "decision",
            "reservation_and_lost_acknowledgement", "restart_recovery",
            "late_fill_and_unpaired_inventory", "operator_view",
            "settlement", "reconciled_economics", "command_centre"]
        print("\n" + LABEL + "\n" + json.dumps(trace, indent=1, default=str))
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 4 · THE ATOMICITY, EACH BOUNDARY AS ITS OWN FAILURE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_conflicting_reservation_bind_leaves_no_intent_row(monkeypatch):
    """BOUNDARY 1. The reservation already names a DIFFERENT intent, so the
    intent insert must roll back -- otherwise the lane holds an intent in the
    one-position slot with no reservation naming it, while the reservation goes
    on counting the same collateral."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        _transport(monkeypatch, order_id="venue-x",
                   executions=[_fill(10, HEDGE_PX, vid="vf-x")])
        # A REAL, LEGAL FIRST BIND. The reservation claims the hedge leg and is
        # committed to a hedge intent on that very slug -- so the database's own
        # leg-matching check is satisfied and the only thing left to refuse the
        # SECOND bind is the identity rule this boundary rests on.
        first = await FB.record_intent(
            conn, intent_id="fpi-pairlife-hedge-first", account_id=ACCT,
            venue=VENUE, venue_class=FA.VENUE_FUNDED,
            us_market_slug=SLUG_HEDGE, event_key=EVENT, order_intent=FX.LONG,
            limit_price=HEDGE_PX, quantity=HEDGE_QTY, collateral_usd=4.10,
            effective_digest="d", payout_event="CAROLINA_PANTHERS_PLUS_4_5",
            held_is_long=True, portfolio_group_id=GROUP, leg_role="HEDGE")
        assert first["ok"] is True, first
        h = await RSV.hold(conn, operation_id=OP_HEDGE, group_id=GROUP,
                           leg_role="HEDGE", us_market_slug=SLUG_HEDGE,
                           quantity=HEDGE_QTY, limit_price=HEDGE_PX,
                           collateral_usd=4.10)
        assert h["ok"] is True, h
        bound = await RSV.commit_to_intent(
            conn, operation_id=OP_HEDGE, intent_id="fpi-pairlife-hedge-first")
        assert bound["ok"] is True, bound
        # AND THAT FIRST INTENT IS THEN ABANDONED, so the one-open-leg-per-role
        # bound does not refuse the second insert FIRST. That refusal is also
        # correct -- and it would make this test pass for the wrong reason,
        # asserting nothing about the bind. The reservation deliberately keeps
        # naming the abandoned intent: that is the state an interrupted
        # acquisition actually leaves behind.
        await FB.abandon_before_send(
            conn, "fpi-pairlife-hedge-first",
            "abandoned so this test can reach the bind rather than the "
            "one-open-leg-per-role index")
        intents_before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT)
        got = await FX.submit_for_decision(
            conn, _hedge_decision_record(), account_id=ACCT, venue=VENUE,
            venue_positions=EMPTY_VENUE, operation_id=OP_HEDGE,
            portfolio_group_id=GROUP, leg_role="HEDGE")
        assert got["ok"] is False, got
        # ── THE REFUSAL MOVED EARLIER, AND THAT IS THE IMPROVEMENT ───
        #
        # It used to come from `commit_to_intent` inside boundary 1's
        # transaction, which rolled the intent insert back. It now comes from the
        # RISK BOUNDARY, before any arithmetic: carrying the operation identity
        # into `check_rails` meant the reading had to look the reservation up,
        # and a reservation past HELD is a replay whatever else is true. Boundary
        # 1's rollback is still there and still correct -- it is now
        # belt-and-braces behind a guard that fires first, which is the right
        # order for two checks of the same thing.
        assert got["refusal"] == RSV.R_NOT_HELD_SO_ALREADY_SUBMITTED, got
        assert got["nothing_was_written"] is True
        assert got["intent_id"] is None
        assert got["exposure"] == "NONE"
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT) == intents_before, (
            "the refusal left an intent row behind, holding a position slot no "
            "reservation names")
        # AND THE FIRST BIND IS UNTOUCHED: the reservation still names the
        # intent it was committed to.
        after = await RSV.get(conn, OP_HEDGE)
        assert after["reservation"]["intent_id"] == "fpi-pairlife-hedge-first"
        assert after["reservation"]["state"] == RSV.COMMITTED



    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_boundary_one_rolls_the_intent_back_when_its_insert_fails(
        monkeypatch):
    """BOUNDARY 1, REACHED THE OTHER WAY.

    With the replay guard now firing first, the way to exercise boundary 1's
    rollback from this entry point is an intent insert that FAILS while the
    reservation is legitimately HELD and matches the plan. A second live leg in
    the same role does it -- 131 permits one open entry per role per group.

    THE PROPERTY IS THE SAME EITHER WAY: the refusal leaves no intent row and no
    committed reservation, so the lane is not left holding a position slot that
    nothing names.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        _, sent, client = _transport(
            monkeypatch, order_id="venue-b1",
            executions=[_fill(HEDGE_QTY, HEDGE_PX, vid="vf-b1")])
        # A LIVE HEDGE LEG ALREADY OCCUPIES THE ROLE.
        blocker = await FB.record_intent(
            conn, intent_id="fpi-pairlife-b1-blocker", account_id=ACCT,
            venue=VENUE, venue_class=FA.VENUE_FUNDED,
            us_market_slug=SLUG_HEDGE, event_key=EVENT, order_intent=FX.LONG,
            limit_price=HEDGE_PX, quantity=HEDGE_QTY,
            collateral_usd=FX.collateral_for(HEDGE_PX, HEDGE_QTY, FX.LONG),
            effective_digest="d", payout_event="CAROLINA_PANTHERS_PLUS_4_5",
            held_is_long=True, portfolio_group_id=GROUP, leg_role="HEDGE")
        assert blocker["ok"] is True, blocker
        # AND A CLEAN, MATCHING, *HELD* RESERVATION for the same leg.
        coll = FX.collateral_for(HEDGE_PX, HEDGE_QTY, FX.LONG)
        h = await RSV.hold(conn, operation_id=OP_LOST, group_id=GROUP,
                           leg_role="HEDGE", us_market_slug=SLUG_HEDGE,
                           quantity=HEDGE_QTY, limit_price=HEDGE_PX,
                           collateral_usd=coll)
        assert h["ok"] is True, h
        before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT)
        got = await FX.submit_for_decision(
            conn, _hedge_decision_record(), account_id=ACCT, venue=VENUE,
            venue_positions=EMPTY_VENUE, operation_id=OP_LOST,
            portfolio_group_id=GROUP, leg_role="HEDGE")
        assert got["ok"] is False, got
        assert got["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE, got
        assert got["nothing_was_written"] is True
        assert got["intent_id"] is None
        assert got["exposure"] == "NONE"
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT) == before
        # THE RESERVATION IS STILL HELD AND NAMES NO INTENT: the bind did not
        # half-happen.
        after = await RSV.get(conn, OP_LOST)
        assert after["reservation"]["state"] == RSV.HELD
        assert after["reservation"]["intent_id"] is None
        assert client.orders.creates == 0, "nothing may reach the venue"
    finally:
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE intent_id=$1",
            "fpi-pairlife-b1-blocker")
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_reservation_refuses_a_second_claim_on_the_same_leg():
    """THE DUPLICATE-ACQUISITION GUARD, AT THE DATABASE. A second cycle
    reaching for a leg an AMBIGUOUS reservation still claims is refused by the
    row, not by a check that races it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        first = await RSV.hold(conn, operation_id=OP_HEDGE, group_id=GROUP,
                               leg_role="HEDGE", us_market_slug=SLUG_HEDGE,
                               quantity=HEDGE_QTY, limit_price=HEDGE_PX,
                               collateral_usd=4.10)
        assert first["ok"] is True, first
        second = await RSV.hold(conn, operation_id="op:another-cycle",
                                group_id=GROUP, leg_role="HEDGE",
                                us_market_slug=SLUG_HEDGE,
                                quantity=HEDGE_QTY, limit_price=HEDGE_PX,
                                collateral_usd=4.10)
        assert second["ok"] is False
        assert second["refusal"] == RSV.R_LEG_ALREADY_RESERVED
        await conn.execute(
            "DELETE FROM bettor_funded_leg_reservations WHERE operation_id=$1",
            "op:another-cycle")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_pair_pass_recovers_even_with_no_pairing_inputs():
    """THE SHIPPED CONFIGURATION. `pair_inputs=None` is what the scheduled
    worker passes, and the pass must still run recovery -- which reads only our
    own rows -- and NAME the absent input rather than reporting success."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        got = await PC.pass_once(conn, account_id=ACCT, venue=VENUE,
                                 pair_inputs=None)
        assert got["ok"] is True, got
        assert got["paired_anything"] is False
        assert got["refusal"] == PC.R_NO_HELD_POSITION
        assert got["recovery"]["ok"] is True
        assert got["recovery"]["resubmitted_anything"] is False
        assert got["opened_anything"] is False
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# ONE MANAGEMENT DECISION BEFORE ANY ACTION, THROUGH `pass_once`
# ═════════════════════════════════════════════════════════════════════
#
# "Make one management decision before executing an action: reconcile first,
# then rank HOLD/DIRECT_EXIT/REDUCE/indirect together, then dispatch one
# selected action. No sale may precede that comparison."
#
# WHAT THE EXISTING PROOF DOES NOT COVER. The lifecycle test above walks the
# stages by calling `discover`, `decide_and_record` and `acquire_second_leg` in
# order -- which demonstrates each stage but cannot demonstrate that the
# PRODUCTION CALLER puts them in that order, because the test supplies the
# order. These two go through `pass_once` with a supplier, so the ordering under
# test is the module's.

def _processing_delay_bound() -> float:
    """The DEPLOYED bound, read from the worker rather than restated here.

    A literal copied into this file would drift from the constant the scheduled
    caller actually applies, and the test would then assert an expiry production
    does not use.
    """
    from sportsassets.workers import ext_pinnacle_loop as _W

    return float(_W.MAX_OUR_PROCESSING_DELAY_S)


def _pair_facts(hold_ranking=None):
    """Everything `pass_once`'s supplier contract requires, for one position.

    `pair_inputs` is deliberately not defaulted in the module -- every field is
    a reading of a venue, a fee schedule or an odds source, and the module
    inventing one would be manufacturing the inputs of a capital decision. So
    the test plays the supplier, and this function is the whole contract in one
    place.
    """
    # ── THE MEASURE IS STATED OVER THE PARTITION THIS SUPPLIER DECLARES ──
    #
    # THE INCONSISTENCY THIS CLOSES, AND IT WAS THE FIXTURE'S OWN. The supplier
    # below declares `fixture_can_void: False`, and `pass_once` hands that to
    # BOTH `discover` and the whole-position valuation, so the pass values the
    # position over NINE regions. This call omitted the flag, took the default
    # `True`, and the probabilities built from it named TEN -- including
    # "fixture cancelled or abandoned", a region the supplier had just said
    # cannot occur.
    #
    # `bettor_funded_decision` now requires exactly one probability per region
    # of the table it values, and refused this with
    # PROBABILITIES_DO_NOT_MATCH_THE_OUTCOME_PARTITION. That refusal was right:
    # a measure over a different outcome space is not a measure of this one.
    # The hedge then carried no value into the comparison, and the ranking test
    # read EXIT as the winner of a contest the hedge could not enter.
    #
    # Measured, not assumed: under MATCHING flags the classifier's table and the
    # position's table are identical region for region (10 = 10 with void, 9 = 9
    # without), so production -- which passes one reading to both -- is
    # consistent. Only this fixture disagreed with itself.
    admitted = PC.discover(held_leg=_held_leg(),
                           candidate_legs=_decoys() + [_hedge_leg()],
                           sport_permits_tie=False,
                           fixture_can_void=False,
                           fixture_can_postpone=False)["admitted"][0]

    async def _supply(conn, pos, *, at):
        return {
            "ok": True,
            "held_leg": _held_leg(),
            "candidate_legs": _decoys() + [_hedge_leg()],
            "sport_permits_tie": False,
            "fixture_can_void": False,
            "fixture_can_postpone": False,
            "decision_id": DECISION,
            "hold_ranking": hold_ranking or _hold_ranking(),
            "region_probabilities": _region_probabilities(
                admitted["structure"]["table"]),
            "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
            "fee_usd": HEDGE_FEE,
            # ── THE QUOTE IS KEYED BY THE SIDE IT WAS TAKEN FROM ─────
            #
            # WHY THIS IS NOW REQUIRED, AND WHY ITS ABSENCE WAS A REAL GAP IN
            # THIS FIXTURE RATHER THAN A NEW STRICTNESS TO ROUTE AROUND. One
            # venue instrument carries TWO outcome tokens with different
            # acquisition prices, so a reading keyed by `market_slug` alone
            # cannot price either side. `rank_admitted` records such a reading
            # as SLUG-ONLY and `decision_options` then refuses the candidate
            # with SIDE_SPECIFIC_QUOTE_NOT_ESTABLISHED.
            #
            # This supplier gave only the flat `fee_usd` and `depth` below, so
            # the hedge was dropped from the comparison BEFORE any number was
            # compared -- `hedge_decision_inputs.candidate_ids` measured empty.
            # The ranking test below then read a winner chosen from a set the
            # hedge had never joined. A candidate excluded for want of a quote
            # and a candidate beaten on its value are different facts, and
            # asserting either against this fixture required supplying this.
            #
            # `candidate_id` is `slug#SIDE` and matches `_hedge_leg()`, so the
            # price here IS this side's own.
            # `inputs_expire_at` is the second thing the plan requires, and for
            # the same reason: a priced, sized order plan that outlives its
            # inputs would send a stale limit. Production takes
            # `min(book_state_established_at + bound, read_at + MAX_OUR_
            # PROCESSING_DELAY_S)` in `_candidate_quote`. This test reads no
            # venue, so it supplies the SECOND term of that same expression off
            # the supplier's own clock -- a bound this fixture can honestly
            # state, rather than a currency guarantee it has not established.
            "candidate_leg_details": [
                {"candidate_id": _hedge_leg().condition_id,
                 "price": HEDGE_PX, "depth_qty": 25, "fee_usd": HEDGE_FEE,
                 "inputs_expire_at": float(at) + _processing_delay_bound()}],
            "depth": FIP.depth_supports(wanted_qty=HEDGE_QTY,
                                        depth_qty_at_price=25),
            "incremental": FIP.incremental_capital_usd(
                hedge_qty=HEDGE_QTY, hedge_price=HEDGE_PX,
                hedge_fee_usd=HEDGE_FEE),
            "capital_duration_h": 26.0,
            "holding_policy": FL.POLICY_HOLD_TO_SETTLEMENT,
            "limits": None,
            "operation_id": OP_HEDGE,
            "hedge_us_market_slug": SLUG_HEDGE,
            "hedge_quantity": HEDGE_QTY,
            "hedge_limit_price": HEDGE_PX,
            "hedge_collateral_usd": FX.collateral_for(HEDGE_PX, HEDGE_QTY,
                                                      FX.LONG),
            # ── NONE, AS IN PRODUCTION ───────────────────────────────
            #
            # This supplied `_hedge_decision_record()` -- a LONG at 0.41 -- while
            # the ranking selected the SHORT side at a wire price of 0.59. The
            # old `setdefault` merge let that record override the ranked plan,
            # so this test was exercising a path production never takes (the
            # scheduled supplier sets it to None) and an order production must
            # never send. The admission record is now BUILT FROM THE PLAN, so the
            # scheduled caller needs no separate order and must not supply a
            # contradicting one. `test_a_supplied_record_that_disagrees_with_the_
            # plan_is_refused` pins what happens when one is supplied anyway.
            "hedge_decision_record": None,
        }

    return _supply


def _exit_is_the_standalone_winner():
    """The same three alternatives, with DIRECT_EXIT on top.

    HOLD +0.10, DIRECT_EXIT +2.40, REDUCE +0.30. A lane that ranked only these
    three and dispatched the winner would SELL. That is the case the review
    asked to see beaten by the combined ranking.
    """
    hr = _hold_ranking()
    for c in hr["candidates"]:
        if c["action"] == "HOLD":
            c["value_usd"] = c["expected_net_usd"] = 0.10
        elif c["action"] == "DIRECT_EXIT":
            c["value_usd"] = c["expected_net_usd"] = 2.40
            c["downside_usd"] = 2.40
        elif c["action"] == "REDUCE":
            c["value_usd"] = c["expected_net_usd"] = 0.30
    return hr


# ── THE SAME CONTRACT, PRICED AS PRODUCTION NOW REQUIRES FOR REAL MONEY ──
#
# WHY THESE EXIST (880377f). A funded order goes out only when the common
# (one-measure) valuation permits it and selects the same fixed action: HOLD,
# the exits and the acquisition valued on ONE distribution over the held
# contract's own payouts, over the void rate's measured range. That needs
# (a) HOLD's probability, quantity and basis, (b) each exit's net proceeds,
# (c) the held leg's settlement terms and (d) an acquisition priced through
# the payout-state distribution from an APPROVED conditional model on a
# MEASURED void rate. `_pair_facts` above supplies a hand-written region
# measure and a HOLD with no probability, so a hedge it ranks first is
# correctly refused funded dispatch. These supply the production inputs;
# `tests/approved_conditional_model.approve` must have run first. SYNTHETIC.
P_PRIMARY = 0.55


def _robust_hold_ranking(*, exit_px=0.66, reduce_qty=5):
    """HOLD, DIRECT_EXIT and REDUCE priced on ONE probability (0.55) and the
    position's real basis (0.62), in `rank_with_hold`'s shape: HOLD -0.70,
    DIRECT_EXIT at 0.66 +0.40 (profitable -- the best standalone action),
    REDUCE 5 -0.15. The exit beats HOLD at both ends of any void range (a
    void pays 50c, below the 66c bid)."""
    q, b = float(PRIMARY_QTY), PRIMARY_PX
    hold = round(q * P_PRIMARY - q * b, 6)
    ex_cash = round(q * exit_px, 6)
    rd_cash = round(reduce_qty * exit_px, 6)
    return {"version": "MGMT_SELECT_SHAPE", "not_rankable": [],
            "candidates": [
                {"action": "HOLD", "qty": q, "value_usd": hold,
                 "expected_net_usd": hold, "value_per_contract": P_PRIMARY,
                 "basis_per_contract_valued": b, "downside_usd": -q * b,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
                 "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                 "execution_secured": True},
                {"action": "DIRECT_EXIT", "qty": q,
                 "value_usd": round(ex_cash - q * b, 6),
                 "expected_net_usd": round(ex_cash - q * b, 6),
                 "downside_usd": round(ex_cash - q * b, 6),
                 "cash_now_usd": ex_cash, "limit_price": exit_px,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 0.0,
                 "evidence_quality": FD.EVIDENCE_VENUE_IMPLIED,
                 "execution_secured": False},
                {"action": "REDUCE", "qty": float(reduce_qty),
                 "value_usd": round(rd_cash + (q - reduce_qty) * P_PRIMARY
                                    - q * b, 6),
                 "expected_net_usd": round(rd_cash + (q - reduce_qty)
                                           * P_PRIMARY - q * b, 6),
                 "downside_usd": round(rd_cash - q * b, 6),
                 "cash_now_usd": rd_cash, "limit_price": exit_px,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
                 "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                 "execution_secured": False}]}


def _distribution_pair_facts(hold_ranking=None, *, calibration=None,
                             hedge_px=HEDGE_PX, depth_qty=25,
                             hedge_fee=HEDGE_FEE):
    """`_pair_facts`, with the inputs the production supplier passes for a
    distribution-priced acquisition: the held leg's terms, a fixture that CAN
    void (so the measured void rate is used), HOLD's primary probability with
    its source and that source's calibration (read by the worker's own
    reader; read on the pass's own connection when not given), and the two
    legs' costs as the features' inputs."""
    from tests import approved_conditional_model as ACM

    hedge = dataclasses.replace(_hedge_leg(),
                                cost_cents_per_unit=int(round(hedge_px * 100)))
    base = _pair_facts(hold_ranking or _robust_hold_ranking())

    async def _supply(conn, pos, *, at):
        facts = await base(conn, pos, at=at)
        cal = (calibration if calibration is not None
               else await ACM.calibration(conn))
        facts.update({
            "candidate_legs": _decoys() + [hedge],
            "fixture_can_void": True,
            "region_probabilities": None,
            "fee_usd": hedge_fee,
            "candidate_leg_details": [
                {"candidate_id": hedge.condition_id, "price": hedge_px,
                 "depth_qty": depth_qty, "fee_usd": hedge_fee,
                 "inputs_expire_at": float(at) + _processing_delay_bound()}],
            "depth": FIP.depth_supports(wanted_qty=HEDGE_QTY,
                                        depth_qty_at_price=depth_qty),
            "incremental": FIP.incremental_capital_usd(
                hedge_qty=HEDGE_QTY, hedge_price=hedge_px,
                hedge_fee_usd=hedge_fee),
            "model_inputs": {
                "primary_cost_cents": int(PRIMARY_PX * 100),
                "hedge_cost_cents": int(round(hedge_px * 100)),
                "overtime_included": True,
                "primary_probability": P_PRIMARY,
                "primary_source": ACM.primary_source(payout_event=PAYS_ON),
                "primary_calibration": cal},
        })
        return facts
    return _supply


async def test_the_pass_ranks_everything_together_before_it_dispatches(
        monkeypatch):
    """THE ORDERING, FROM THE PRODUCTION CALLER.

    The exit is the best standalone action and is profitable, so the sale is the
    one a three-way lane would make. Through `pass_once` the indirect candidate
    joins the same ranking, the acquisition wins, and the exit is still in the
    ranked set having been beaten on its number rather than withheld.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from tests import approved_conditional_model as ACM
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        _, sent, _client = _transport(monkeypatch, order_id="venue-hedge")
        # THE PRODUCTION INPUTS FOR A FUNDED ACQUISITION (880377f): an
        # approved conditional model and a measured void rate (SYNTHETIC
        # observations), HOLD on one probability and the real basis, the
        # exit profitable and the best standalone action -- see
        # `_robust_hold_ranking`.
        await ACM.approve(conn)

        got = await PC.pass_once(
            conn, account_id=ACCT, venue=VENUE,
            pair_inputs=_distribution_pair_facts(
                _robust_hold_ranking(),
                calibration=await ACM.calibration(conn)),
            venue_positions=EMPTY_VENUE)
        assert got["ok"] is True, got
        step = got["considered"][0]

        # ── RECONCILIATION RAN FIRST, and it needs no supplier ───────
        assert got["recovery"]["ok"] is True

        # ── ONE RANKING, AND THE ACQUISITION WON IT ──────────────────
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        assert step["decision"]["policy"] == "EXPECTED_NET_VALUE"

        # ── THE EXIT WAS RANKED, NOT WITHHELD ───────────────────────
        row = await conn.fetchrow(
            "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
            DECISION)
        assert row is not None, "the decision is recorded before the action"
        assert row["action"] == "ACQUIRE_HEDGE"

        # ── AND EXACTLY ONE ACTION WAS DISPATCHED ───────────────────
        assert len(got["acquisitions"]) == 1, got["acquisitions"]
        assert got["opened_anything"] is True
        # ONE CREATE. The adapter previews before it creates, so `sent` carries
        # both calls and counting the list would have counted the preview as a
        # second order -- which is how a test comes to assert the wrong number
        # and then get "fixed" by relaxing it.
        creates = [c for c in sent if c[0] == "create"]
        assert len(creates) == 1, sent
        # AND REAL MONEY FOLLOWED A ROBUST, AGREEING ONE-MEASURE CHOICE
        assert step["funded_dispatch_gate"]["permitted"] is True, step
        assert step["common_valuation"]["selection_basis"] == \
            "ROBUST_ACROSS_THE_VOID_RATE_RANGE"
    finally:
        await ACM.purge(conn)
        await _clean(conn)
        await conn.close()


async def test_a_decision_that_is_not_an_acquisition_reaches_no_venue(
        monkeypatch):
    """NO SALE PRECEDES THE COMPARISON, AND NONE FOLLOWS IT FROM HERE EITHER.

    With HOLD winning, `pass_once` records the decision and dispatches nothing.
    The adapter is armed and counts its sends, so an unexpected order would be
    visible rather than inferred from a return value.

    AND IT DOCUMENTS A REAL LIMIT OF THIS PATH, which I would rather state than
    have read as coverage: `pass_once` can dispatch ONLY the acquisition. A
    selected DIRECT_EXIT or REDUCE is recorded and left to the entry lane's own
    management path -- so "dispatch one selected action" is implemented for one
    of the four actions, not four. The refusal name says so explicitly instead
    of the step falling silent.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        _, sent, _client = _transport(monkeypatch, order_id="venue-hedge")

        # HOLD MUST ACTUALLY WIN, and the file's default ranking does not make
        # it win -- the hedge beats HOLD at +1.80 on these facts, which is what
        # the lifecycle test above relies on. So HOLD is raised above the
        # hedge's own expected net value rather than the hedge being weakened,
        # because weakening the hedge would test a different thing.
        hr = _hold_ranking()
        for c in hr["candidates"]:
            if c["action"] == "HOLD":
                c["value_usd"] = c["expected_net_usd"] = 9.50
        got = await PC.pass_once(
            conn, account_id=ACCT, venue=VENUE,
            pair_inputs=_pair_facts(hr),
            venue_positions=EMPTY_VENUE)
        assert got["ok"] is True, got
        step = got["considered"][0]
        assert step["decision"]["action"] != PC.ACTION_ACQUIRE, step
        assert step["refusal"] == PC.R_DECISION_IS_NOT_ACQUIRE
        assert step["what_was_selected_instead"] == step["decision"]["action"]

        # THE COMPARISON HAPPENED AND WAS WRITTEN.
        row = await conn.fetchrow(
            "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
            DECISION)
        assert row is not None

        # AND NOTHING WENT TO THE VENUE.
        assert got["acquisitions"] == []
        assert got["opened_anything"] is False
        assert [c for c in sent if c[0] == "create"] == [], sent
        held = await RSV.reserved_collateral_usd(conn, account_id=ACCT)
        assert held["ok"] is True and held["reserved_usd"] == 0.0, (
            "a decision not to acquire must leave no reservation behind")
    finally:
        await _clean(conn)
        await conn.close()
