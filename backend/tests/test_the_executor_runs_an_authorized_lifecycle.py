"""AUTHORIZATION THROUGH EXECUTION, on a TEST venue, end to end.

WHAT THIS FILE IS FOR. Everything upstream decided whether a submission MAY
happen. This drives the path that actually does it: submit, acknowledgement,
partial fill, cancellation, recovery after a restart, and accounting
recomputed from the ledger. And it proves the negative side -- an expired, a
mismatched and a revoked authorization each stop the submission, by name,
with nothing written.

WHAT IS NOT CLAIMED. No venue sandbox credential is connected, so the venue
here is `SimulatedTestVenue`, which says on the object that it is a simulator
and reaches no network. The EXECUTOR is real: the same code, with a sandbox
adapter behind the same four methods, is what would talk to a venue.

FUNDED SUBMISSION STAYS OFF. A FUNDED-class venue is refused before an
adapter is touched, and the execution gate's last check is the code constant.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_desk as dk
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_test_venue_executor as TX

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

ACCT = "acct-pilot-test-001"
VENUE = "PMUS_TEST"
CID = "0x" + "ee" * 32
SLUG = "tvx-lifecycle-2026-09-26"
LIMITS = {"capital_usd": 250, "per_order_usd": 25,
          "max_exposure_usd": 100, "daily_loss_stop_usd": 50}


# ── the contract, without a database ────────────────────────────────

def test_the_executor_only_runs_a_test_class_venue():
    d = TX.describe()
    assert d["allowed_venue_classes"] == [FA.VENUE_TEST]
    assert d["funded_submission"] == "DISABLED"
    assert d["asks_before_every_action"] == \
        "bettor_entry_execution.authorize_submission"
    assert d["recovery_never_resubmits"] is True
    # AND IT SAYS WHAT IT IS NOT.
    assert "no venue sandbox credential is connected" in \
        d["what_is_not_claimed"]
    assert set(d["venue_interface"]) == {"submit", "poll", "cancel",
                                         "open_orders"}


def test_the_simulator_admits_that_it_is_one():
    v = TX.SimulatedTestVenue()
    assert v.is_a_simulator is True
    assert v.reaches_a_real_venue is False
    assert "SIMULATED" in v.name


def test_the_book_is_not_a_strategy_book():
    from sportsassets import bettor_capacity_fingerprint as P
    from sportsassets import bettor_demonstration as DEMO
    from sportsassets import bettor_external_shadow as ext

    assert TX.EXPERIMENT not in P.STRATEGY_EXPERIMENTS
    assert TX.EXPERIMENT != ext.EXPERIMENT_ID
    assert TX.EXPERIMENT != DEMO.EXPERIMENT


# ── helpers ─────────────────────────────────────────────────────────

async def _seed_account(conn):
    await conn.execute(
        """
        INSERT INTO bettor_desk_accounts
          (account_id, desk_id, status, opening_balance, opened_at, note,
           provenance, paused, pause_reason, accounting_status,
           accounting_detail)
        VALUES ($1,'desk-2','ACTIVE',0, now(),'a clean test-venue pilot',
                '{}'::jsonb, FALSE, NULL,'CLEAN','{}'::jsonb)
        ON CONFLICT (account_id) DO UPDATE
           SET status = 'ACTIVE', paused = FALSE,
               accounting_status = 'CLEAN'
        """, ACCT)


async def _authorize(conn, **over):
    """Write an authorization record of the shape `authorize()` writes."""
    eff = EX.effective_limits(LIMITS)
    at = float(over.pop("at", time.time()))
    rec = {"account_id": ACCT, "venue": VENUE, "venue_class": FA.VENUE_TEST,
           "by": "test", "at": at,
           "expires_at": at + EX.AUTHORIZATION_TTL_S,
           "revoked": False,
           "effective_limits": eff["effective"],
           "effective_digest": eff["effective_digest"],
           "funded_submission": "DISABLED"}
    rec.update(over)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        FA.AUTHORIZATION_KEY, json.dumps(rec))
    # and the approved limit set the gate digests against
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        FA.LIMITS_KEY, json.dumps({"proposed": dict(LIMITS),
                                   "approved": True,
                                   "approved_by": "OWNER"}))
    return rec


async def _wipe(conn):
    await conn.execute(
        "DELETE FROM rn1x_fills WHERE order_id IN (SELECT o.order_id FROM "
        "rn1x_orders o JOIN rn1x_positions p ON p.position_id = "
        "o.position_id WHERE p.experiment_id = $1)", TX.EXPERIMENT)
    await conn.execute(
        "DELETE FROM rn1x_orders WHERE position_id IN (SELECT position_id "
        "FROM rn1x_positions WHERE experiment_id = $1)", TX.EXPERIMENT)
    # DECISIONS BEFORE POSITIONS: rn1x_decisions.position_id is a foreign key
    # into rn1x_positions, so the other order leaves the position undeletable.
    await conn.execute(
        "DELETE FROM rn1x_decisions WHERE position_id IN (SELECT position_id "
        "FROM rn1x_positions WHERE experiment_id = $1)", TX.EXPERIMENT)
    await conn.execute("DELETE FROM rn1x_positions WHERE experiment_id = $1",
                       TX.EXPERIMENT)
    await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                       [FA.AUTHORIZATION_KEY, FA.LIMITS_KEY])


# ── THE COMPLETE LIFECYCLE ──────────────────────────────────────────

@pg
async def test_the_whole_lifecycle_submit_ack_partial_cancel_recover_reconcile():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        await _seed_account(conn)
        await _authorize(conn)
        v = TX.SimulatedTestVenue(partial_ratio=0.4)

        # 1 · SUBMIT. The gate is asked first and the ACK is recorded.
        got = await TX.submit(conn, v, account_id=ACCT, venue=VENUE,
                              condition_id=CID, slug=SLUG, qty=100.0,
                              limit_price=0.50)
        assert got["ok"] is True, got
        assert got["submitted"] is True
        assert got["state"] == dk.RESTING
        assert got["venue_order_id"].startswith("sim-")
        assert got["authorization"]["authorization_consumed"] is True
        assert got["funded_submission"] == "DISABLED"
        oid = got["order_id"]
        # the row says RESTING and carries the venue's id
        row = dict(await conn.fetchrow(
            "SELECT state, decision_id, venue_order_id, qty::float8 AS qty, "
            "filled_qty::float8 AS filled_qty FROM rn1x_orders "
            "WHERE order_id = $1", oid))
        assert row["state"] == dk.RESTING
        # TWO IDENTITIES, TWO COLUMNS: ours links to the decision, the
        # venue's is what a poll, a cancel and recovery use.
        assert row["venue_order_id"] == got["venue_order_id"]
        assert row["decision_id"] == got["decision_id"]
        assert row["decision_id"] != row["venue_order_id"]
        assert row["filled_qty"] == 0.0

        # 2 · A PARTIAL FILL, taken from the venue and priced with the
        #     deployed fee schedule.
        poll = await TX.poll_once(conn, v, account_id=ACCT, venue=VENUE)
        assert poll["ok"] is True, poll
        o = poll["orders"][0]
        assert o["venue_state"] == dk.PARTIALLY_FILLED
        assert o["filled_qty"] == pytest.approx(40.0)
        assert o["remaining"] == pytest.approx(60.0)
        assert len(o["fills_written"]) == 1
        f = o["fills_written"][0]
        assert f["fee_usd"] == pytest.approx(TX.fee_for(40.0, 0.50))
        assert f["fee_usd"] > 0

        # a second poll fills nothing more: the venue's schedule is fixed
        again = await TX.poll_once(conn, v, account_id=ACCT, venue=VENUE)
        assert again["orders"][0]["fills_written"] == []
        assert again["orders"][0]["filled_qty"] == pytest.approx(40.0)

        # 3 · RECOVERY AFTER A RESTART. A NEW executor object, the same
        #     database and the same venue: our rows are reconciled against
        #     the venue's own open orders, and nothing is re-sent.
        before = await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1",
            TX.EXPERIMENT)
        rec = await TX.recover(conn, v, account_id=ACCT, venue=VENUE)
        assert rec["ok"] is True, rec
        assert rec["resubmitted_anything"] is False
        assert rec["orphans"] == []
        assert rec["reconciled"][0]["case"] == "OPEN_AT_BOTH"
        assert rec["reconciled"][0]["adopted_filled_qty"] == \
            pytest.approx(40.0)
        after = await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1",
            TX.EXPERIMENT)
        assert after == before, "recovery created an order"

        # 4 · CANCEL THE REMAINDER, through CANCEL_PENDING to CANCELLED.
        can = await TX.cancel_open(conn, v, account_id=ACCT, venue=VENUE)
        assert can["ok"] is True, can
        assert can["cancelled"][0]["requested"] == dk.CANCEL_PENDING
        assert can["cancelled"][0]["state_now"] == dk.CANCELLED
        state = await conn.fetchval(
            "SELECT state FROM rn1x_orders WHERE order_id = $1", oid)
        assert state == dk.CANCELLED

        # 5 · RECONCILED ACCOUNTING, recomputed from the ledger.
        acc = await TX.reconcile(conn)
        assert acc["orders"] == 1
        assert acc["fills"] == 1
        assert acc["filled_qty"] == pytest.approx(40.0)
        assert acc["notional_usd"] == pytest.approx(20.0)
        assert acc["fees_reconcile"] is True
        assert acc["fees_usd"] == pytest.approx(acc["expected_fees_usd"])
        assert acc["discrepancy_count"] == 0, acc["discrepancies"]
        per = acc["per_order"][0]
        assert per["submitted_qty"] == pytest.approx(100.0)
        assert per["filled_qty_from_fills"] == pytest.approx(40.0)
        assert per["filled_qty_on_the_order"] == pytest.approx(40.0)
        assert per["remaining"] == pytest.approx(60.0)
        assert per["state"] == dk.CANCELLED
        assert acc["states"] == {dk.CANCELLED: 1}
        assert acc["this_book_is_not_strategy_performance"] is True

        # 6 · AND NOTHING REACHED live_orders.
        assert await conn.fetchval(
            "SELECT count(*) FROM live_orders WHERE order_id LIKE 'tvx-%'"
        ) == 0
    finally:
        await _wipe(conn)
        await conn.close()


@pg
async def test_a_venue_rejection_is_recorded_and_not_swallowed():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        await _seed_account(conn)
        await _authorize(conn)
        v = TX.SimulatedTestVenue(reject_next=True)
        got = await TX.submit(conn, v, account_id=ACCT, venue=VENUE,
                              condition_id=CID, slug=SLUG)
        assert got["ok"] is False
        assert got["refusal"] == TX.R_VENUE_REJECTED
        assert got["rejected_because"] == "SIMULATED_VENUE_REJECTION"
        assert got["state"] == dk.REJECTED
        # THE ROW EXISTS AND SAYS REJECTED. An order we sent and the venue
        # refused is a fact, not an absence.
        state = await conn.fetchval(
            "SELECT state FROM rn1x_orders WHERE order_id = $1",
            got["order_id"])
        assert state == dk.REJECTED
        # and it is not an open order
        assert await TX._open_orders(conn) == []
    finally:
        await _wipe(conn)
        await conn.close()


# ── THE NEGATIVE SIDE: NO AUTHORIZATION, NO SUBMISSION ──────────────

@pg
async def test_expired_mismatched_and_revoked_authorization_all_stop_it():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        await _seed_account(conn)
        v = TX.SimulatedTestVenue()

        async def _try():
            got = await TX.submit(conn, v, account_id=ACCT, venue=VENUE,
                                  condition_id=CID, slug=SLUG)
            n = await conn.fetchval(
                "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
                "ON p.position_id = o.position_id WHERE p.experiment_id = $1",
                TX.EXPERIMENT)
            return got, int(n)

        # NO RECORD AT ALL
        got, n = await _try()
        assert got["ok"] is False
        assert got["refusal"] == EX.R_NO_AUTHORIZATION
        assert n == 0, "an unauthorized submit wrote an order row"
        assert v.calls == [], "an unauthorized submit reached the venue"

        # EXPIRED -- granted 25 hours ago, TTL 24 h
        await _authorize(conn, at=time.time() - 25 * 3600)
        got, n = await _try()
        assert got["refusal"] == EX.R_AUTH_EXPIRED, got
        assert got["authorization"]["authorization_consumed"] is False
        assert n == 0 and v.calls == []

        # MISMATCHED ACCOUNT
        await _authorize(conn, account_id="someone-else")
        got, n = await _try()
        assert got["refusal"] == EX.R_AUTH_ACCOUNT, got
        assert n == 0 and v.calls == []

        # MISMATCHED VENUE
        await _authorize(conn, venue="SANDBOX")
        got, n = await _try()
        assert got["refusal"] == EX.R_AUTH_VENUE, got
        assert n == 0 and v.calls == []

        # MISMATCHED LIMITS -- the approved set moved after the grant
        await _authorize(conn)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            FA.LIMITS_KEY, json.dumps({"proposed": dict(LIMITS,
                                                        per_order_usd=999),
                                       "approved": True}))
        got, n = await _try()
        assert got["refusal"] == EX.R_AUTH_LIMITS, got
        assert n == 0 and v.calls == []

        # REVOKED
        await _authorize(conn, revoked=True, revoked_by="OWNER",
                         revoked_at=time.time())
        got, n = await _try()
        assert got["refusal"] == EX.R_AUTH_REVOKED, got
        assert n == 0 and v.calls == []

        # AND A FUNDED VENUE IS REFUSED BEFORE THE ADAPTER EXISTS
        await _authorize(conn, venue="PMUS", venue_class=FA.VENUE_FUNDED)
        got = await TX.submit(conn, v, account_id=ACCT, venue="PMUS",
                              condition_id=CID, slug=SLUG)
        assert got["refusal"] == TX.R_VENUE_CLASS_NOT_ALLOWED, got
        assert v.calls == []

        # FINALLY, THE SAME CALL WITH A GOOD RECORD SUCCEEDS -- so every
        # refusal above was the authorization and not a broken executor.
        await _authorize(conn)
        ok = await TX.submit(conn, v, account_id=ACCT, venue=VENUE,
                             condition_id=CID, slug=SLUG)
        assert ok["ok"] is True, ok
        assert len(v.calls) == 1 and v.calls[0][0] == "submit"
    finally:
        await _wipe(conn)
        await conn.close()


@pg
async def test_poll_and_cancel_are_gated_too():
    """THE GATE IS IN FRONT OF EVERY ACTION, not only the first one. An
    authorization revoked mid-flight stops the next action."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        await _seed_account(conn)
        await _authorize(conn)
        v = TX.SimulatedTestVenue()
        got = await TX.submit(conn, v, account_id=ACCT, venue=VENUE,
                              condition_id=CID, slug=SLUG)
        assert got["ok"] is True
        calls = len(v.calls)

        # REVOKE IT MID-FLIGHT
        await _authorize(conn, revoked=True, revoked_by="OWNER")
        for fn in (TX.poll_once, TX.cancel_open, TX.recover):
            r = await fn(conn, v, account_id=ACCT, venue=VENUE)
            assert r["ok"] is False, (fn.__name__, r)
            assert r["refusal"] == EX.R_AUTH_REVOKED, (fn.__name__, r)
        assert len(v.calls) == calls, "a revoked session still reached the venue"

        # THE ORDER IS LEFT EXACTLY AS IT WAS.
        row = dict(await conn.fetchrow(
            "SELECT state, filled_qty::float8 AS filled_qty FROM rn1x_orders "
            "WHERE order_id = $1", got["order_id"]))
        assert row["state"] == dk.RESTING
        assert row["filled_qty"] == 0.0
    finally:
        await _wipe(conn)
        await conn.close()


@pg
async def test_an_orphan_at_the_venue_is_reported_and_never_adopted():
    """A venue order this book has no row for is the most dangerous thing
    recovery can meet. It is reported and left alone."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        await _seed_account(conn)
        await _authorize(conn)
        v = TX.SimulatedTestVenue()
        # an order at the venue that we never recorded
        v.submit({"client_order_id": "not-ours", "condition_id": CID,
                  "outcome_index": 0, "side": "BUY", "qty": 10.0,
                  "limit_price": 0.5, "venue": VENUE})
        rec = await TX.recover(conn, v, account_id=ACCT, venue=VENUE)
        assert rec["ok"] is True
        assert len(rec["orphans"]) == 1, rec
        assert "never adopted" in rec["orphans"][0]["what"]
        # nothing was written for it
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1",
            TX.EXPERIMENT) == 0
    finally:
        await _wipe(conn)
        await conn.close()
