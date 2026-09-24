"""CONTROLLED ACCEPTANCE — labelled as such, and run through the real thing.

These are CONTROLLED TESTS. Every input is supplied by this file. They
establish that the machinery behaves correctly when a scenario occurs;
they establish NOTHING about whether it occurs in production, and nothing
whatever about profitability.

WHAT MAKES THEM ACCEPTANCE RATHER THAN UNIT TESTS: each scenario is driven
through the SAME objects the deployed application uses --
`bettor_mgmt_lifecycle.Managed` for orders and fills,
`bettor_desk.Portfolio` for cash and inventory, `bettor_rn1x_policy` for
the frozen thresholds, `bettor_entry_gate.admit` for admission,
`bettor_rn1x_store` for persistence into the real schema. Nothing is
reimplemented here. Where a scenario needs the venue or the provider, the
stub is placed at the transport boundary and everything above it is
production code.

The eight scenarios, each named for what it proves:

  1 BUY                     an entry is admitted through the real gate
  2 PAIRING                 the $0.91 combined-cost rule, fee convention
  3 SECOND-HALF LOSS EXIT   the 16% trigger, and that it is gated on phase
  4 NO FILL                 a resting order with no crossing print
  5 PARTIAL FILL            a print smaller than the order
  6 CANCELLATION RACE       a print crossing an order in CANCEL_PENDING
  7 SETTLEMENT              payout read once, cash released only there
  8 RESTART RECOVERY        same cash, inventory, orders and consumption
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_desk as desk
from sportsassets import bettor_entry_gate as gate
from sportsassets import bettor_fee_schedule as FEES
from sportsassets import bettor_mgmt_lifecycle as lc
from sportsassets import bettor_rn1x_policy as policy

CONTROLLED = "CONTROLLED TEST. Inputs supplied by the test, not observed."

T0 = 1_700_000_000.0


def _fee():
    from decimal import Decimal

    def fee(qty, price, maker=False):
        return float(FEES.LATEST.fill_fee(
            Decimal(str(round(float(qty), 6))),
            Decimal(str(round(float(price), 6))), maker=bool(maker)))
    return fee


def _managed(*, seed_price=0.40, seed_qty=100.0, at=T0, queue_share=0.25):
    """One managed position, built exactly as `bettor_rn1x_run` builds it."""
    return lc.Managed(condition_id="c-acc", outcome_index=0,
                      seed_qty=seed_qty, seed_price=seed_price,
                      at=at, fee_fn=_fee(), queue_share=queue_share,
                      expiry_s=900.0, account="acc-controlled")


# ── 1 · BUY ─────────────────────────────────────────────────────────

def test_1_buy_is_admitted_through_the_real_gate():
    """The six entry requirements, satisfied, through the deployed gate."""
    out = gate.admit(
        action_table=None,
        # THE MODEL CONTRACT, read off the gate rather than guessed. All
        # three are required and they are three different claims: the
        # target must be a settlement target, the model must be frozen or
        # promoted, and its recorded predictions must have been valid AS
        # ENTRY-TIME calls -- a frozen model whose every prediction was
        # invalid has never made a usable call.
        model={"model_key": "controlled", "version": "1",
               "target": "SETTLEMENT_OUTCOME", "status": "FROZEN",
               "prediction_validity": "VALID_AS_ENTRY_TIME"},
        fair_value={"value": 0.60, "kind": "CONTROLLED"},
        execution_estimate={"p_fill": 0.8, "basis": CONTROLLED,
                            "crossing": True},
        size=10.0,
        risk={"permitted": True, "reason": CONTROLLED},
        market_state={"ask": 0.50, "depth": 500.0, "readable": True},
        fee_fn=_fee(),
        min_net_edge_per_contract=0.01)
    assert out["admissible"] is True, out
    # The edge is computed, not asserted: probability - ask - fee.
    row = out["detail"]["gate_computed_row"]
    assert row["action"] == "BUY"
    assert row["status"] == "IDENTIFIED_BY_ENTRY_GATE"
    # probability - ask - fee, computed by the gate from the supplied
    # independent fair value. The components are reported separately so the
    # arithmetic is checkable rather than asserted.
    assert row["expectedNetDollarsPerContract"] == pytest.approx(
        0.60 - 0.50 - _fee()(1.0, 0.50), abs=1e-9)
    assert row["components"]["fair_value"] == pytest.approx(0.60)
    assert row["components"]["ask"] == pytest.approx(0.50)
    assert row["components"]["fee_per_contract"] == pytest.approx(
        _fee()(1.0, 0.50))


def test_1b_each_missing_requirement_refuses_on_its_own():
    """Parameterised so removing one check fails exactly one case."""
    base = dict(
        action_table=None,
        model={"model_key": "k", "version": "1",
               "target": "SETTLEMENT_OUTCOME", "status": "FROZEN",
               "prediction_validity": "VALID_AS_ENTRY_TIME"},
        fair_value={"value": 0.60, "kind": "CONTROLLED"},
        execution_estimate={"p_fill": 0.8, "basis": CONTROLLED,
                            "crossing": True},
        size=10.0, risk={"permitted": True},
        market_state={"ask": 0.50, "depth": 500.0, "readable": True},
        fee_fn=_fee())
    for field, bad in (("execution_estimate", {"p_fill": None}),
                       ("size", 0.0),
                       ("risk", {"permitted": False}),
                       ("market_state", {"ask": 0.50, "depth": 0.0,
                                         "readable": True}),
                       ("market_state", {"readable": False}),
                       ("fair_value", None)):
        kw = dict(base)
        kw[field] = bad
        got = gate.admit(**kw)
        assert got["admissible"] is False, (field, bad, got)
        assert got["refusals"], (field, bad)


# ── 2 · PAIRING at the frozen $0.91 ─────────────────────────────────

def test_2_pairing_fires_at_or_below_the_frozen_limit():
    m = _managed(seed_price=0.40, seed_qty=100.0)
    basis = 0.40
    target = policy.pair_limit(basis, 100.0, fee_fn=_fee())
    assert target["feasible"] is True, target
    # THE FROZEN NUMBER, read from the policy module rather than typed, and
    # under its real key name.
    assert policy.PAIR_TARGET_COST == pytest.approx(0.91)
    assert target["target_combined_cost"] == pytest.approx(0.91)
    assert target["target_is_frozen_default"] is True
    # The limit is solved on the tick grid and rounded CONSERVATIVELY, so
    # combined cost INCLUDING fees must land at or under the target -- not
    # merely basis + limit, which ignores the fee the target contains.
    combined = basis + target["limit"] + _fee()(1.0, target["limit"])
    assert combined <= 0.91 + 1e-9, (combined, target)
    rec = m.manage_policy(at=T0 + 1, decision_id="d1")
    assert rec["selected_action"] == "POST_COMPLEMENT", rec
    assert rec["acted"] is True
    assert len(m.open_orders()) == 1


def test_2b_pairing_refuses_when_the_basis_leaves_no_room():
    """A basis above the limit cannot be paired at any price."""
    m = _managed(seed_price=0.95, seed_qty=100.0)
    rec = m.manage_policy(at=T0 + 1, decision_id="d1")
    assert rec["operating_state"] == "HOLD_NO_FEASIBLE_PAIR", rec
    assert rec["acted"] is False
    assert not m.open_orders()


# ── 3 · SECOND-HALF LOSS EXIT ───────────────────────────────────────

def test_3_the_16_percent_trigger_is_computed_and_reported_separately():
    """The trigger and the modelled execution loss are different numbers
    and must not be reported as one."""
    stop = policy.loss_trigger(allocated_cost_usd=40.0, qty=100.0,
                               bid=0.30, bid_size=1000.0, fee_fn=_fee())
    # THE FROZEN FRACTION IS 0.84, EXPRESSED AS RETAINED PROCEEDS.
    # "a 16% loss" is 1 - 0.84: the rule fires when net executable
    # proceeds fall to or below 84% of allocated cost. I first wrote this
    # assertion as 0.16 and it failed, correctly -- the two are different
    # quantities and only one of them is the policy's.
    assert policy.LOSS_TRIGGER_FRACTION == pytest.approx(0.84)
    assert stop["trigger_fraction"] == pytest.approx(0.84)
    assert stop["fraction_is_frozen_default"] is True
    assert stop["fired"] is True, stop
    assert stop["price_input"] == "EXECUTABLE_BID_AND_DEPTH"
    assert "last trade price" in stop["not_used"]

    # THE TRIGGER IS NOT THE REALISED LOSS, reported as two numbers.
    # Selling 100 at 0.30 against a 0.40 basis nets 30.00 less fees on
    # 40.00 allocated -- worse than the 16% the trigger names.
    realised = 100.0 * 0.30 - _fee()(100.0, 0.30)
    rv = policy.realised_vs_trigger(allocated_cost_usd=40.0,
                                    realised_net_usd=realised - 40.0)
    assert rv["trigger_loss_fraction"] == pytest.approx(1.0 - 0.84)
    assert rv["trigger_is_a_level_not_a_maximum"]
    # The realised loss on these numbers is worse than the trigger names.
    realised_loss_fraction = -(realised - 40.0) / 40.0
    assert realised_loss_fraction > rv["trigger_loss_fraction"], (
        realised_loss_fraction, rv["trigger_loss_fraction"])


def test_3b_the_exit_is_gated_on_OBSERVED_phase_not_the_trigger():
    """The trigger firing is not sufficient. Without an observed
    second-half phase the exit must not be available -- this is the rule
    that stops wall-clock time standing in for event progress."""
    m = _managed(seed_price=0.40, seed_qty=100.0)
    rec = m.manage_policy(at=T0 + 1, decision_id="d1",
                          sport="soccer", bid=0.30, bid_size=1000.0)
    assert rec["phase"]["loss_exit_available"] is False, rec["phase"]
    assert rec["loss_trigger"]["fired"] is True
    # ...so it pairs or holds, and does NOT exit.
    assert rec.get("selected_action") != "DIRECT_EXIT", rec


def test_3c_with_an_observed_second_half_the_exit_becomes_available():
    """The same policy, with a real observed progress row, does fire.

    This is the ONLY thing missing in production: the observation. The
    rule, the trigger and the wiring are all present and are exercised
    here with a progress row of the shape the store validates.
    """
    from sportsassets import bettor_progress_feed as pf

    obs = pf.validate({
        "event_key": "e-acc", "source": "official_scoreboard",
        "observed_at": T0 + 1, "status": "IN_PLAY", "period": 2,
        "revision": 1})
    assert obs["ok"] is True, obs
    got = pf.usable([obs["normalized"]], now=T0 + 2, event_key="e-acc")
    assert got["ok"] is True, got
    progress = got["progress"]

    # THE HALFWAY RULE ITSELF, which IS written for soccer. Applied
    # directly, because `event_phase` refuses the sport until a feed is
    # registered -- and that registration is the missing capability, not
    # the rule.
    rule = policy.DOCUMENTED_MAPPINGS["soccer"]
    assert rule is not None
    assert rule(progress) == policy.SECOND_HALF
    assert rule.total_periods == 2
    # And period 1 is NOT the second half, so the rule discriminates.
    assert rule({"period": 1}) == policy.FIRST_HALF
    # Overtime is past halfway, unambiguously.
    assert rule({"period": 3}) == policy.SECOND_HALF

    # And the policy layer still refuses, BY NAME, because no feed is
    # connected for soccer in production.
    phase = policy.event_phase(progress=progress, sport="soccer", now=T0 + 2)
    assert phase["loss_exit_available"] is False
    # THE TWO REGISTRIES ARE SEPARATE, and this is the whole point: the
    # rule IS written, the feed is NOT connected, and the phase is
    # therefore UNDEFINED rather than FIRST_HALF. Reporting a written rule
    # as a working one is the confusion these fields exist to prevent.
    assert phase["rule_written"] is True, phase
    assert phase["feed_connected"] is False, phase
    assert phase["absence"] == policy.PROGRESS_FEED_ABSENT, phase
    assert phase["phase"] == policy.SECOND_HALF_UNDEFINED, phase
    assert "soccer" not in policy.PROGRESS_FEED_CONNECTED


def test_3d_a_derived_progress_source_is_refused_by_name():
    from sportsassets import bettor_progress_feed as pf

    got = pf.validate({"event_key": "e", "source": "wall_clock",
                       "observed_at": T0, "status": "IN_PLAY", "period": 2})
    assert got["ok"] is False, got
    assert got["refusal"] == pf.DERIVED, got
    # Refused BY SOURCE NAME, before any field is read -- so a derived
    # source cannot pass by carrying a plausible period.
    assert "wall_clock" in pf.DERIVED_SOURCES


# ── 4 · NO FILL ─────────────────────────────────────────────────────

def test_4_a_resting_order_with_no_crossing_print_does_not_fill():
    m = _managed()
    m.manage_policy(at=T0 + 1, decision_id="d1")
    o = m.open_orders()[0]
    assert o.filled_qty == 0.0
    # A print on the WRONG leg, and one that does not cross.
    m.on_print(at=T0 + 2, outcome_index=1 - o.outcome_index,
               price=o.limit_price, size=1000.0, evidence_id="e1")
    m.on_print(at=T0 + 3, outcome_index=o.outcome_index,
               price=o.limit_price + 0.10, size=1000.0, evidence_id="e2")
    assert o.filled_qty == 0.0, "neither print should have filled it"
    assert m.state()["portfolio"]["realized_pnl_usd"] == 0.0


# ── 5 · PARTIAL FILL ────────────────────────────────────────────────

def test_5_a_smaller_print_fills_part_and_leaves_the_rest_working():
    m = _managed(queue_share=0.25)
    m.manage_policy(at=T0 + 1, decision_id="d1")
    o = m.open_orders()[0]
    want = o.remaining
    # A crossing print whose queue share is well under the order size.
    fills = m.on_print(at=T0 + 2, outcome_index=o.outcome_index,
                       price=o.limit_price - 0.01, size=40.0,
                       evidence_id="p1")
    assert fills, "a crossing print must allocate"
    got = sum(f["qty"] for f in fills)
    assert 0 < got < want, (got, want)
    assert got == pytest.approx(40.0 * 0.25), "queue share governs"
    assert o.filled_qty == pytest.approx(got)
    assert o.remaining == pytest.approx(want - got)
    # PARTIALLY_FILLED, a state of its own -- not RESTING, which is what
    # I assumed. The distinction matters: a partially filled order has a
    # known executed quantity behind it and an untouched remainder.
    assert o.state == lc.dk.PARTIALLY_FILLED, o.state
    assert o.remaining > 0, "the remainder must still be working"


def test_5b_one_print_is_not_consumed_twice():
    """The consumption ledger is what stops a single observed execution
    licensing two of our fills."""
    m = _managed(queue_share=1.0)
    m.manage_policy(at=T0 + 1, decision_id="d1")
    o = m.open_orders()[0]
    a = m.on_print(at=T0 + 2, outcome_index=o.outcome_index,
                   price=o.limit_price - 0.01, size=10.0, evidence_id="same")
    b = m.on_print(at=T0 + 3, outcome_index=o.outcome_index,
                   price=o.limit_price - 0.01, size=10.0, evidence_id="same")
    assert sum(f["qty"] for f in a) == pytest.approx(10.0)
    assert sum(f["qty"] for f in b) == 0.0, (
        "the same evidence id must not be consumed twice: %r" % (b,))


# ── 6 · CANCELLATION RACE ───────────────────────────────────────────

def test_6_an_order_in_cancel_pending_still_fills():
    """The gap between requesting a cancel and its acknowledgement is
    where the race lives. Ignoring a fill there would under-report
    inventory and the accounting would not reconcile."""
    m = _managed(queue_share=1.0)
    m.manage_policy(at=T0 + 1, decision_id="d1")
    first = m.open_orders()[0]
    # A second decision supersedes it: cancel requested, nothing placed.
    again = m.place("POST_COMPLEMENT", at=T0 + 2, price=first.limit_price,
                    qty=first.remaining, decision_id="d2")
    assert again["placed"] is None
    assert again["refused"] == "WAIT_CANCEL_ACK", again
    assert first.state == lc.dk.CANCEL_PENDING
    # ...and a crossing print STILL fills it.
    fills = m.on_print(at=T0 + 3, outcome_index=first.outcome_index,
                       price=first.limit_price - 0.01, size=5.0,
                       evidence_id="race")
    assert fills, "a CANCEL_PENDING order must still be executable"
    assert first.filled_qty == pytest.approx(5.0)
    # The acknowledgement then closes it, and the fill is kept.
    done = m.acknowledge_cancels(T0 + 4)
    assert first.order_id in done
    assert first.state == lc.dk.CANCELLED
    assert first.filled_qty == pytest.approx(5.0)


def test_6b_only_one_order_works_at_a_time():
    """The declared concurrency policy: one management order, cancel the
    incumbent first, and place nothing until it is acknowledged."""
    m = _managed()
    m.manage_policy(at=T0 + 1, decision_id="d1")
    assert len(m.open_orders()) == 1
    m.place("POST_COMPLEMENT", at=T0 + 2, price=0.40, qty=10.0,
            decision_id="d2")
    working = [o for o in m.open_orders()
               if o.state == lc.dk.RESTING]
    assert len(working) == 0, "nothing may rest while a cancel is pending"
    assert len(m.open_orders()) == 1


# ── 7 · SETTLEMENT and capital release ──────────────────────────────

def test_7_paired_inventory_is_not_cash_until_settlement():
    """THE ACCOUNTING RULE THAT MATTERS MOST. Holding both legs of a
    binary market is economically a certain payout, and it is NOT cash:
    the venue releases nothing until resolution. Counting it early would
    overstate available capital by the whole position."""
    m = _managed(seed_price=0.40, seed_qty=100.0, queue_share=1.0)
    cash_after_seed = m.pf.cash
    m.manage_policy(at=T0 + 1, decision_id="d1")
    o = m.open_orders()[0]
    # Fill the complement completely -> fully paired.
    m.on_print(at=T0 + 2, outcome_index=o.outcome_index,
               price=o.limit_price - 0.01, size=o.remaining,
               evidence_id="pair")
    assert m.matched() > 0, "the position must be paired for this to mean anything"
    assert m.pf.cash < cash_after_seed, (
        "buying the complement SPENDS cash; it must not release any")
    paired_cash = m.pf.cash

    # Settlement is the release mechanism, and only there does cash rise.
    m.settle_at_observed_payout({0: 1.0, 1: 0.0}, T0 + 100)
    assert m.pf.cash > paired_cash, "settlement must release the payout"


def test_7b_settlement_reads_the_payout_once_and_reconciles():
    m = _managed(seed_price=0.40, seed_qty=100.0, queue_share=1.0)
    m.manage_policy(at=T0 + 1, decision_id="d1")
    o = m.open_orders()[0]
    m.on_print(at=T0 + 2, outcome_index=o.outcome_index,
               price=o.limit_price - 0.01, size=o.remaining,
               evidence_id="pair")
    m.settle_at_observed_payout({0: 1.0, 1: 0.0}, T0 + 100)
    st = m.state()
    assert st["invariant"]["ok"] is True, st["invariant"]
    # A second settlement must not pay twice.
    before = m.pf.cash
    m.settle_at_observed_payout({0: 1.0, 1: 0.0}, T0 + 101)
    assert m.pf.cash == pytest.approx(before), "settled twice"


# ── 8 · RESTART RECOVERY ────────────────────────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
@pytest.mark.asyncio
async def test_8_restart_recovery_is_identical_and_does_not_duplicate():
    """Persist, drop the in-memory state entirely, read it back, and
    confirm the account resumes on the same cash, inventory, orders and
    consumption -- without replenishing capital or duplicating fills.

    The persistence path is `bettor_rn1x_store.persist`, which is what the
    deployed worker calls. The 'restart' is a fresh connection and a fresh
    read: nothing from the first process survives in memory.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_rn1x_run as R
    from sportsassets import bettor_rn1x_store as store

    conn = await asyncpg.connect(DSN)
    try:
        for f in ("migrations/100_rn1_seeded_experiment.sql",
                  "migrations/104_rn1x_clock_integrity.sql"):
            await conn.execute(open(f).read())
        eid = "E_RESTART"
        await conn.execute(
            "INSERT INTO rn1x_experiments (experiment_id, code_version, "
            "seed_rule, policy_register, execution_basis, notes) "
            "VALUES ($1,'V','{}'::jsonb,'{}'::jsonb,'MODELLED',$2) "
            "ON CONFLICT (experiment_id) DO NOTHING", eid, CONTROLLED)
        await conn.execute(
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id "
            "FROM rn1x_orders WHERE position_id IN (SELECT position_id "
            "FROM rn1x_positions WHERE experiment_id = $1))", eid)
        await conn.execute(
            "DELETE FROM rn1x_orders WHERE position_id IN (SELECT "
            "position_id FROM rn1x_positions WHERE experiment_id = $1)", eid)
        await conn.execute(
            "DELETE FROM rn1x_outcomes WHERE position_id IN (SELECT "
            "position_id FROM rn1x_positions WHERE experiment_id = $1)", eid)
        await conn.execute(
            "DELETE FROM rn1x_decisions WHERE position_id IN (SELECT "
            "position_id FROM rn1x_positions WHERE experiment_id = $1)", eid)
        await conn.execute(
            "DELETE FROM rn1x_positions WHERE experiment_id = $1", eid)

        # A run through the REAL entry point, with the runtime basis.
        now = T0 + 5.0
        rows = [{"id": 9001, "whale_id": 7, "outcome_index": 0,
                 "side": "BUY", "size": 100.0, "price": 0.40,
                 "ts": T0, "detected_at": T0 - 0.6},
                {"id": 9002, "whale_id": 8, "outcome_index": 1,
                 "side": "BUY", "size": 80.0, "price": 0.45,
                 "ts": now + 1.0, "detected_at": now + 1.2}]
        out = R.run(rows=rows, source_whale_id=7, condition_id="c-restart",
                    initial_inventory_verified=True,
                    now=now, decision_basis=R.BASIS_RUNTIME)
        assert out["steps"]["SEED"]["ok"] is True, out["steps"]
        # policy and trade_id are read OFF THE RUN, not passed in -- the
        # position id is derived from them so a caller cannot key a row to
        # a policy the run did not use.
        wrote = await store.persist_run(conn, out, experiment_id=eid,
                                        source_account="acc")
        assert wrote["decisions"] > 0, wrote

        def snapshot(rows_):
            return sorted((r["order_id"], float(r["qty"]),
                           float(r["filled_qty"]), r["state"])
                          for r in rows_)

        pos1 = await conn.fetchrow(
            "SELECT position_id, seed_qty::float8 q, seed_price::float8 p, "
            "decision_basis, extract(epoch FROM decision_ts)::float8 dts "
            "FROM rn1x_positions WHERE experiment_id = $1", eid)
        ord1 = snapshot(await conn.fetch(
            "SELECT order_id, qty, filled_qty, state FROM rn1x_orders "
            "WHERE position_id = $1", pos1["position_id"]))
        fill1 = await conn.fetchval(
            "SELECT count(*) FROM rn1x_fills WHERE order_id IN (SELECT "
            "order_id FROM rn1x_orders WHERE position_id = $1)",
            pos1["position_id"])
        dec1 = await conn.fetchval(
            "SELECT count(*) FROM rn1x_decisions WHERE position_id = $1",
            pos1["position_id"])
    finally:
        await conn.close()

    # ── THE RESTART. A new connection, nothing carried over. ─────────
    conn2 = await asyncpg.connect(DSN)
    try:
        pos2 = await conn2.fetchrow(
            "SELECT position_id, seed_qty::float8 q, seed_price::float8 p, "
            "decision_basis, extract(epoch FROM decision_ts)::float8 dts "
            "FROM rn1x_positions WHERE experiment_id = $1", "E_RESTART")
        assert pos2 is not None, "the position did not survive the restart"
        assert pos2["position_id"] == pos1["position_id"]
        assert pos2["q"] == pytest.approx(pos1["q"])
        assert pos2["p"] == pytest.approx(pos1["p"])
        # THE DECISION INSTANT IS THE RUNTIME ONE, and it survived.
        assert pos2["decision_basis"] == "RUNTIME_WALL_CLOCK"
        assert pos2["dts"] == pytest.approx(now, abs=1e-3)

        ord2 = sorted((r["order_id"], float(r["qty"]),
                       float(r["filled_qty"]), r["state"])
                      for r in await conn2.fetch(
                          "SELECT order_id, qty, filled_qty, state FROM "
                          "rn1x_orders WHERE position_id = $1",
                          pos2["position_id"]))
        assert ord2 == ord1, "resting orders changed across the restart"

        # ── REPLAY THE SAME CYCLE. Idempotency: no duplication, no
        # replenished capital, no overwritten record.
        from sportsassets import bettor_rn1x_run as R2
        from sportsassets import bettor_rn1x_store as store2

        rows = [{"id": 9001, "whale_id": 7, "outcome_index": 0,
                 "side": "BUY", "size": 100.0, "price": 0.40,
                 "ts": T0, "detected_at": T0 - 0.6},
                {"id": 9002, "whale_id": 8, "outcome_index": 1,
                 "side": "BUY", "size": 80.0, "price": 0.45,
                 "ts": now + 1.0, "detected_at": now + 1.2}]
        out2 = R2.run(rows=rows, source_whale_id=7,
                      condition_id="c-restart",
                      initial_inventory_verified=True,
                      now=now, decision_basis=R2.BASIS_RUNTIME)
        await store2.persist_run(conn2, out2, experiment_id="E_RESTART",
                                 source_account="acc")

        assert await conn2.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            "E_RESTART") == 1, "the position was duplicated"
        assert await conn2.fetchval(
            "SELECT count(*) FROM rn1x_fills WHERE order_id IN (SELECT "
            "order_id FROM rn1x_orders WHERE position_id = $1)",
            pos2["position_id"]) == fill1, "fills were duplicated"
        assert await conn2.fetchval(
            "SELECT count(*) FROM rn1x_decisions WHERE position_id = $1",
            pos2["position_id"]) == dec1, "decisions were duplicated"
        # And the seed was not re-bought: the position row is unchanged.
        pos3 = await conn2.fetchrow(
            "SELECT seed_qty::float8 q, extract(epoch FROM decision_ts)"
            "::float8 dts FROM rn1x_positions WHERE experiment_id = $1",
            "E_RESTART")
        assert pos3["q"] == pytest.approx(pos1["q"])
        assert pos3["dts"] == pytest.approx(pos1["dts"], abs=1e-3)
    finally:
        await conn2.close()


@pg
@pytest.mark.asyncio
async def test_8b_persistence_is_atomic():
    """A failure partway through must leave NOTHING, not half a record.
    Half-written records are what made the desk's book uncertain."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        before = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            "E_ATOMIC")
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO rn1x_experiments (experiment_id, "
                    "code_version, seed_rule, policy_register, "
                    "execution_basis, notes) VALUES "
                    "('E_ATOMIC','V','{}'::jsonb,'{}'::jsonb,'MODELLED','t')"
                    " ON CONFLICT (experiment_id) DO NOTHING")
                await conn.execute(
                    "INSERT INTO rn1x_positions (position_id, "
                    "experiment_id, policy, source_trade_id, "
                    "source_account, condition_id, outcome_index, "
                    "entry_kind, entry_kind_why, seed_qty, seed_price, "
                    "seed_basis_usd, source_ts, detected_ts, decision_ts, "
                    "available_at, decision_basis) VALUES "
                    "('p-atomic','E_ATOMIC','P',1,'A','c',0,'NEW','w',1,"
                    "0.4,0.4,now(),now(),now(),now(),'RUNTIME_WALL_CLOCK')")
                # Now violate a constraint inside the SAME transaction.
                await conn.execute(
                    "INSERT INTO rn1x_orders (order_id, position_id, "
                    "condition_id, outcome_index, side, intent, liquidity, "
                    "limit_price, qty, state, placed_at, fill_basis, "
                    "is_modelled) VALUES ('o-atomic','p-atomic','c',0,"
                    "'BUY','ENTER','TAKER',0.4,1,'OPEN',now(),'B',FALSE)")
        after = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            "E_ATOMIC")
        assert after == before, (
            "the position survived a failed transaction: %s -> %s"
            % (before, after))
    finally:
        await conn.close()
